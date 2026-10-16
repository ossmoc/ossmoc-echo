"""Read bounded, identity-bearing English references from a release ZIP.

These are source references only. Vietnamese targets must come from the
separately hash-verified canonical English-name PAK of the same release.
"""
import re
import sqlite3
import zipfile
import hashlib
import json
from pathlib import PurePosixPath

from ludiglot.core.source_records import text_rows, fingerprint


_SOURCE = re.compile(r'^editor/build_support/TextSource/(\d+)_source/[^/]+\.txt$')
_VERSION = re.compile(r'^(\d+)\.(\d+)_part\d+\.txt$')
_RAW = re.compile(r'^editor/build_support/Wahu/_dbcfg/Client/Content/Aki/ConfigDB/(en|zh-Hans|zh-CN)/(.+\.db)$')


def raw_candidates(archive, canonical, check_cancel):
    """Same-release bilingual references, preserving relative DB/table/ID.

    Empty/masked redirects do not authorize a split-file guess. Conflicting
    Chinese locales and unrecognised schemas are recorded/rejected.
    """
    sources = {}
    inventory = []
    total = 0
    seen = set()
    with zipfile.ZipFile(archive) as package:
        if len(package.infolist()) > 100000:
            raise ValueError('Gói nguồn có quá nhiều tệp.')
        for entry in package.infolist():
            match = _RAW.fullmatch(entry.filename)
            if not match:
                continue
            check_cancel()
            lang, name = match.groups()
            parts = PurePosixPath(name)
            total += entry.file_size
            if (parts.is_absolute() or '..' in parts.parts or any(':' in p or '\\' in p for p in parts.parts)
                    or entry.filename.casefold() in seen or entry.flag_bits & 1
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or entry.file_size > 256*1024**2 or total > 768*1024**2):
                raise ValueError('Nguồn SQLite trong ZIP không an toàn, trùng hoặc quá lớn.')
            seen.add(entry.filename.casefold())
            data = package.read(entry)
            conn = sqlite3.connect(':memory:')
            try:
                conn.deserialize(data)
                conn.execute('PRAGMA trusted_schema=OFF')
                conn.execute('PRAGMA query_only=ON')
                if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise ValueError('Nguồn SQLite không toàn vẹn.')
                rows = list(text_rows(conn,name,check_cancel))
            finally:
                conn.close()
            inventory.append({'language':lang,'database':name,'sha256':hashlib.sha256(data).hexdigest(),'rows':len(rows)})
            for row in rows:
                sources.setdefault(row.identity,{})[lang] = row
    candidates = []
    reasons = {}
    for identity, languages in sources.items():
        check_cancel()
        en = languages.get('en')
        chinese = [languages[l] for l in ('zh-Hans','zh-CN') if l in languages]
        reason = ''
        if not en or not chinese:
            reason = 'missing_bilingual_source'
        elif len({fingerprint(r.text) for r in chinese}) != 1:
            reason = 'chinese_locale_conflict'
        elif not en.text.strip() or not chinese[0].text.strip() or set(en.text.strip())=={'*'} or set(chinese[0].text.strip())=={'*'}:
            reason = 'empty_or_masked_source'
        elif en.redirect not in (None,0) or any(r.redirect not in (None,0) for r in chinese):
            reason = 'redirect_mapping_unknown'
        target = (canonical.target(*identity) if hasattr(canonical,'target') else canonical.get((PurePosixPath(identity[0]).name,identity[2]),''))
        if not reason and (not target.strip() or fingerprint(en.text)==fingerprint(target)):
            reason = 'missing_target_or_english_fallback'
        if reason:
            reasons[reason] = reasons.get(reason,0)+1
            continue
        candidates.append((*identity,en.text,chinese[0].text,target,'raw_bilingual_exact',
                           fingerprint(en.text),fingerprint(chinese[0].text)))
    return candidates, {'files':inventory,'rejected':reasons,'accepted':len(candidates)}


def rebuild_from_release_sources(archive, target, canonical, check_cancel):
    candidates, inventory = raw_candidates(archive, canonical, check_cancel)
    references = {}
    masked = {}
    # identity -> (source version, table, English); ambiguity is sticky within
    # a version. Older snapshots cannot overwrite newer source references.
    with zipfile.ZipFile(archive) as package:
        total = 0
        seen = set()
        for entry in package.infolist():
            check_cancel()
            if not _SOURCE.fullmatch(entry.filename):
                continue
            name = entry.filename.rsplit('/', 1)[-1]
            version = _VERSION.fullmatch(name)
            if not version:
                continue
            group = str(int(version[1])) + str(int(version[2]))
            if package_name_group(entry.filename) != group:
                raise ValueError('Thư mục nguồn và phiên bản tệp không khớp.')
            total += entry.file_size
            if (entry.filename.casefold() in seen or entry.flag_bits & 1
                    or entry.file_size > 32*1024**2 or total > 256*1024**2
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Gói nguồn English trùng, quá lớn hoặc không an toàn.')
            seen.add(entry.filename.casefold())
            text = package.read(entry).decode('utf-8-sig')
            source_version = (int(version[1]), int(version[2]))
            def save(header, fields):
                if not header or not fields.get('EN'):
                    return
                dbfile, table, key = header
                identity = (dbfile.casefold(), key)
                if identity not in canonical:
                    return
                english = '\n'.join(fields['EN']).strip()
                chinese = '\n'.join(fields.get('CN',[])).strip()
                if (english and set(english)=={'*'} and chinese and set(chinese)=={'*'}):
                    old=masked.get(identity)
                    value=(source_version,table)
                    if old is None or old[0]<source_version:
                        masked[identity]=value
                    elif old[0]==source_version and old!=value:
                        masked[identity]=(source_version,None)
                if not english or set(english) == {'*'}:
                    return
                value = (source_version, table, english, chinese)
                old = references.get(identity)
                if old is None or old[0] < source_version:
                    references[identity] = value
                elif old[0] == source_version and old != value:
                    references[identity] = (source_version, None, None, None)
            header, fields, field = None, {}, None
            for line in text.splitlines():
                check_cancel()
                if line.startswith('@@ '):
                    save(header, fields)
                    parts = line[3:].split('||')
                    header = tuple(parts) if len(parts) == 3 and all(parts) else None
                    if header and (not re.fullmatch(r'[A-Za-z0-9_.-]+\.db', header[0])
                                   or any('\x00' in part for part in header)):
                        header = None
                    fields, field = {}, None
                else:
                    match = re.match(r'^(CN|EN|VI|HV|JP): ?(.*)$', line)
                    if match:
                        field = match[1]
                        if field in fields:
                            header = None  # Duplicate fields are not trustworthy.
                        fields[field] = [match[2]]
                    elif field:
                        fields[field].append(line)
            save(header, fields)
    rows = []
    for identity, (_version, table, english, _chinese) in references.items():
        check_cancel()
        target_text = canonical.get(identity, '')
        if table and english and target_text.strip() and target_text.strip() != english.strip():
            rows.append((*identity, english, target_text, 'final'))
    from ludiglot.core.release_redirects import story_redirects
    proofs=story_redirects(archive,canonical,masked,{row[:2] for row in rows},check_cancel)
    rows.extend((p[0],p[1],p[5],p[7],'final') for p in proofs)
    if not rows and not candidates:
        raise ValueError('Không có ID với nguồn English rõ ràng trong gói bản dịch; giữ bản cũ.')
    with sqlite3.connect(target) as connection:
        connection.execute('CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)')
        connection.executemany('INSERT INTO meta VALUES (?,?)', [
            ('name_mode','en'), ('derived_from','same_release_sources_and_canonical_en_pak')])
        connection.execute('CREATE TABLE entries(dbfile TEXT,key TEXT,en TEXT,vi TEXT,status TEXT,PRIMARY KEY(dbfile,key))')
        connection.executemany('INSERT INTO entries VALUES (?,?,?,?,?)', rows)
        connection.execute('CREATE TABLE release_source_proofs(dbfile TEXT,key TEXT,source_dbfile TEXT,tbl TEXT,version TEXT,en TEXT,cn TEXT,vi TEXT,PRIMARY KEY(dbfile,key))')
        connection.executemany('INSERT INTO release_source_proofs VALUES (?,?,?,?,?,?,?,?)',proofs)
        connection.execute('CREATE TABLE reference_identity(dbfile TEXT,key TEXT,tbl TEXT,cn TEXT,version TEXT,PRIMARY KEY(dbfile,key))')
        connection.executemany('INSERT INTO reference_identity VALUES (?,?,?,?,?)',
            [(*identity,value[1],value[3],'.'.join(map(str,value[0]))) for identity,value in references.items() if value[1] and value[2]])
        connection.execute('CREATE TABLE source_candidates(namespace TEXT,tbl TEXT,key TEXT,en TEXT,cn TEXT,vi TEXT,proof TEXT,en_hash TEXT,cn_hash TEXT,PRIMARY KEY(namespace,tbl,key))')
        connection.executemany('INSERT INTO source_candidates VALUES (?,?,?,?,?,?,?,?,?)',candidates)
        connection.execute('INSERT INTO meta VALUES (?,?)',('source_inventory',json.dumps(inventory,ensure_ascii=False)))
        with open(archive,'rb') as source_stream:
            archive_hash = hashlib.file_digest(source_stream,'sha256').hexdigest()
        connection.execute('INSERT INTO meta VALUES (?,?)',('source_archive_sha256',archive_hash))
        # Expose only unambiguous identities to older providers. The current
        # provider validates candidates by table + exact EN/CN instead.
        pairs = {}
        for ns,tbl,key,en,cn,vi,proof,eh,ch in candidates:
            pairs.setdefault((PurePosixPath(ns).name,key),[]).append((en,vi))
        occupied = {r[:2] for r in rows}
        connection.executemany('INSERT INTO entries VALUES (?,?,?,?,?)',
            [(*pair,values[0][0],values[0][1],'final') for pair,values in pairs.items() if len(values)==1 and pair not in occupied])
    return target


def package_name_group(name):
    return name.split('/')[-2].split('_')[0]
