"""Bounded, schema-aware source readers. No first-table-wins identities."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import struct
import unicodedata


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def fingerprint(value):
    return hashlib.sha256(' '.join(unicodedata.normalize('NFC', value).split()).encode()).hexdigest()


def namespace(value):
    value = str(value).replace('\\', '/').casefold()
    parts = PurePosixPath(value).parts
    if not parts or '..' in parts or any(':' in p for p in parts):
        raise ValueError('Định danh nguồn không an toàn.')
    return '/'.join(parts)


def database_namespace(language_root, relative):
    """Keep ConfigDB sub-namespaces, not just the leaf database filename."""
    parts=Path(language_root).parent.parts
    lowered=[p.casefold() for p in parts]
    if 'configdb' in lowered:
        offset=len(lowered)-1-lowered[::-1].index('configdb')
        prefix=parts[offset+1:]
    elif 'textmap' in lowered:
        offset=len(lowered)-1-lowered[::-1].index('textmap')
        prefix=('textmap',)+parts[offset+1:]
    else:
        prefix=()
    return namespace('/'.join((*prefix,Path(relative).as_posix())))


def decode_config_json(blob, row_key, *, embedded_id=False, flow=False):
    """Known ConfigDB two-field FlatBuffer (row ID + JSON string), or JSON.

    Follow the vtable/string offsets and length; never scan arbitrary bytes for
    a brace or a plausible string. Reject unsupported wrappers/extra payloads.
    """
    if isinstance(blob, str):
        raw = blob.encode('utf-8')
    elif isinstance(blob, (bytes, bytearray)):
        raw = bytes(blob)
    else:
        raise ValueError('BinData không phải JSON/FlatBuffer được hỗ trợ.')
    if not 1 <= len(raw) <= 8 * 1024**2:
        raise ValueError('BinData quá lớn hoặc rỗng.')
    if not raw.lstrip().startswith((b'{', b'[')):
        try:
            root, = struct.unpack_from('<I', raw)
            if root < 8 or root + 4 > len(raw):
                raise ValueError()
            distance, = struct.unpack_from('<i', raw, root)
            vt = root - distance
            if vt < 4 or vt + 4 > root:
                raise ValueError()
            size, obj = struct.unpack_from('<HH', raw, vt)
            if flow and size == 20 and vt+size <= root:
                id_field = 0
                json_field, = struct.unpack_from('<H',raw,vt+4+6*2)
            elif not flow and size == 8 and vt+size <= root:
                id_field, json_field = struct.unpack_from('<HH',raw,vt+4)
            else:
                raise ValueError()
            if obj < 12 or root + obj > len(raw) or not 4 <= json_field <= obj-4:
                raise ValueError()
            pointer = root + json_field
            offset, = struct.unpack_from('<I', raw, pointer)
            start = pointer + offset
            length, = struct.unpack_from('<I', raw, start)
            if start < root + obj or start+4+length >= len(raw) or raw[start+4+length] != 0:
                raise ValueError()
            # Numeric row IDs are also present in the binary wrapper.
            if not flow and str(row_key).isdigit() and (not 4 <= id_field <= obj-4 or struct.unpack_from('<I',raw,root+id_field)[0] != int(row_key)):
                raise ValueError()
            raw = raw[start+4:start+4+length]
        except (struct.error, ValueError) as exc:
            raise ValueError('Schema BinData chưa được hỗ trợ hoặc sai định danh.') from exc
    value = json.loads(raw.decode('utf-8-sig'))
    if embedded_id and (not isinstance(value, dict) or str(value.get('Id')) != str(row_key)):
        raise ValueError('ID trong BinData không khớp hàng nguồn.')
    return value


@dataclass(frozen=True)
class TextRow:
    database: str
    table: str
    key: str
    text: str
    redirect: int | None = None

    @property
    def identity(self):
        return (self.database, self.table, self.key)


def text_rows(connection, database, check_cancel=lambda: None):
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA trusted_schema=OFF')
    database = namespace(database)
    connection.set_progress_handler(lambda: (check_cancel() or 0), 10000)
    for table, in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        check_cancel()
        if table.startswith('sqlite_'):
            continue
        cols = {r[1].casefold(): r[1] for r in connection.execute('PRAGMA table_info('+quote(table)+')')}
        key = next((cols[n] for n in ('textid','textmapid','key','id') if n in cols), None)
        content = next((cols[n] for n in ('text','content','value') if n in cols), None)
        if not key or not content:
            if 'bindata' in cols:
                continue  # Config metadata is not free-form translated text.
            raise ValueError('Schema văn bản chưa được hỗ trợ: '+database+'/'+table)
        redirect = quote(cols['redirectdbindex']) if 'redirectdbindex' in cols else 'NULL'
        seen = set()
        for row_key, text, destination in connection.execute('SELECT '+quote(key)+','+quote(content)+','+redirect+' FROM '+quote(table)):
            check_cancel()
            if row_key is None or text is None:
                continue
            if not isinstance(text, str) or (destination is not None and not isinstance(destination,int)):
                raise ValueError('Kiểu dữ liệu văn bản không được hỗ trợ: '+database+'/'+table)
            row_key = str(row_key)
            if row_key in seen:
                raise ValueError('ID trùng trong cùng bảng: '+database+'/'+table)
            seen.add(row_key)
            yield TextRow(database,table,row_key,text,destination)


def read_text_file(path, database=None, check_cancel=lambda: None):
    path = Path(path)
    if Path(str(path)+'-wal').exists() and Path(str(path)+'-wal').stat().st_size:
        raise ValueError('Nguồn SQLite còn WAL; hãy đóng tác vụ ghi trước khi đọc.')
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as conn:
        return list(text_rows(conn, database or path.name, check_cancel))


class CanonicalTranslations(dict):
    """Legacy unique basename view plus lossless namespace/table identity."""
    def __init__(self):
        super().__init__()
        self.scoped = {}
        self._collisions = set()

    def add(self, row):
        identity = row.identity
        if identity in self.scoped:
            raise ValueError('Canonical trùng namespace/bảng/ID.')
        self.scoped[identity] = row.text
        pair = (PurePosixPath(row.database).name, row.key)
        if pair in self or pair in self._collisions:
            self.pop(pair,None)
            self._collisions.add(pair)
        else:
            self[pair] = row.text

    def __bool__(self):
        return bool(self.scoped) or dict.__len__(self)>0

    def target(self, database, table, key):
        if self.scoped:
            return self.scoped.get((namespace(database),table,key),'')
        return self.get((PurePosixPath(database).name,key),'')


def resolve_redirect(identity, records, destinations, *, max_hops=8):
    """Only explicit schema-provided index→database maps may resolve redirects.

    No implicit 1→1sthalf mapping. Unknown indices/cycles fail closed.
    """
    visited = set()
    for _ in range(max_hops):
        if identity in visited or identity not in records:
            return None, 'redirect_cycle_or_missing'
        visited.add(identity)
        row = records[identity]
        if row.redirect in (None,0):
            return row, 'direct' if len(visited)==1 else 'verified_redirect'
        destination = destinations.get((row.database,row.redirect))
        if destination is None:
            return None, 'redirect_mapping_unknown'
        identity = (namespace(destination),row.table,row.key)
    return None, 'redirect_depth_exceeded'


def upgrade_cache_identity(cache, root, check_cancel=lambda:None):
    """Backfill old basename-only caches using retained files, not a PAK scan.

    Only an exact bilingual, unique current row recovers a missing identity.
    Ambiguity is explicit and must block the old basename provider path.
    """
    from ludiglot.adapters.wuthering_waves.data_mapper import WutheringDataMapper
    needed={}
    for payload in cache.values():
        for row in payload.get('matches',[]):
            if not row.get('source_namespace') or not row.get('source_table'):
                name=PurePosixPath(str(row.get('source_json') or '').replace('\\','/')).name.casefold()
                if name.endswith('.db'):
                    needed.setdefault(name,set()).add(str(row.get('text_key') or ''))
    candidates={}
    mapper=WutheringDataMapper(Path(root))
    for base in mapper.text_source_roots():
        for path in (base/'en').rglob('*.db') if (base/'en').is_dir() else []:
            check_cancel()
            name=path.name.casefold()
            if name not in needed:
                continue
            rel=database_namespace(base/'en',path.relative_to(base/'en'))
            en=read_text_file(path,rel,check_cancel)
            chinese={}
            for lang in ('zh-Hans','zh-CN'):
                zh=base/lang/path.relative_to(base/'en')
                if zh.is_file():
                    for item in read_text_file(zh,rel,check_cancel):
                        chinese.setdefault(item.identity,set()).add(fingerprint(item.text))
            for item in en:
                if item.key not in needed[name] or len(chinese.get(item.identity,()))!=1:
                    continue
                signature=(name,item.key,fingerprint(item.text),next(iter(chinese[item.identity])))
                candidates.setdefault(signature,set()).add(item.identity[:2])
    migrated=ambiguous=0
    for payload in cache.values():
        for row in payload.get('matches',[]):
            if row.get('source_namespace') and row.get('source_table'):
                continue
            name=PurePosixPath(str(row.get('source_json') or '').replace('\\','/')).name.casefold()
            cn=str(row.get('official_zh') or (row.get('official_cn') if not row.get('translation_source') else '') or '')
            signature=(name,str(row.get('text_key') or ''),fingerprint(str(row.get('official_en') or '')),fingerprint(cn))
            values=candidates.get(signature,())
            if len(values)==1:
                ns,tbl=next(iter(values))
                row.update(source_namespace=ns,source_table=tbl,source_identity_status='recovered_exact_bilingual')
                migrated+=1
            elif len(values)>1:
                row['source_identity_status']='ambiguous'
                ambiguous+=1
    return {'recovered_alias_rows':migrated,'ambiguous_alias_rows':ambiguous}


def intern_match_aliases(cache):
    """JSON repeats the same match under search aliases; share equal records."""
    seen={}
    for payload in cache.values():
        rows=payload.get('matches',[])
        for n,row in enumerate(rows):
            identity=(row.get('source_json'),row.get('source_namespace'),row.get('source_table'),
                      row.get('text_key'),row.get('official_en'),row.get('official_zh'))
            previous=seen.get(identity)
            if previous is not None and previous==row:
                rows[n]=previous
            elif previous is None:
                seen[identity]=row
    return cache


def pack_provenance(cache):
    """Store audit evidence once, with content-addressed references in matches."""
    existing=cache.get('__ossmoc_provenance__',{}).get('proofs',{})
    proofs={}
    seen=set()
    for payload in list(cache.values()):
        for row in payload.get('matches',[]):
            if id(row) in seen:
                continue
            seen.add(id(row))
            proof=row.pop('translation_proof',None)
            old_id=row.get('translation_proof_id')
            if not proof and old_id in existing and row.get('official_vi'):
                proofs[old_id]=existing[old_id]
            if proof:
                raw=json.dumps(proof,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
                identifier=hashlib.sha256(raw).hexdigest()
                proofs[identifier]=proof
                row['translation_proof_id']=identifier
    cache['__ossmoc_provenance__']={'matches':[],'schema':1,'proofs':proofs}
    return cache
