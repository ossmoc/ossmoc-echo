"""Read bounded, identity-bearing English references from a release ZIP.

These are source references only. Vietnamese targets must come from the
separately hash-verified canonical English-name PAK of the same release.
"""
import re
import sqlite3
import zipfile


_SOURCE = re.compile(r'^editor/build_support/TextSource/(\d+)_source/[^/]+\.txt$')
_VERSION = re.compile(r'^(\d+)\.(\d+)_part\d+\.txt$')


def rebuild_from_release_sources(archive, target, canonical, check_cancel):
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
                value = (source_version, table, english)
                old = references.get(identity)
                if old is None or old[0] < source_version:
                    references[identity] = value
                elif old[0] == source_version and old != value:
                    references[identity] = (source_version, None, None)
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
    for identity, (_version, table, english) in references.items():
        check_cancel()
        target_text = canonical.get(identity, '')
        if table and english and target_text.strip() and target_text.strip() != english.strip():
            rows.append((*identity, english, target_text, 'final'))
    from ludiglot.core.release_redirects import story_redirects
    proofs=story_redirects(archive,canonical,masked,{row[:2] for row in rows},check_cancel)
    rows.extend((p[0],p[1],p[5],p[7],'final') for p in proofs)
    if not rows:
        raise ValueError('Không có ID với nguồn English rõ ràng trong gói bản dịch; giữ bản cũ.')
    with sqlite3.connect(target) as connection:
        connection.execute('CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)')
        connection.executemany('INSERT INTO meta VALUES (?,?)', [
            ('name_mode','en'), ('derived_from','same_release_sources_and_canonical_en_pak')])
        connection.execute('CREATE TABLE entries(dbfile TEXT,key TEXT,en TEXT,vi TEXT,status TEXT,PRIMARY KEY(dbfile,key))')
        connection.executemany('INSERT INTO entries VALUES (?,?,?,?,?)', rows)
        connection.execute('CREATE TABLE release_source_proofs(dbfile TEXT,key TEXT,source_dbfile TEXT,tbl TEXT,version TEXT,en TEXT,cn TEXT,vi TEXT,PRIMARY KEY(dbfile,key))')
        connection.executemany('INSERT INTO release_source_proofs VALUES (?,?,?,?,?,?,?,?)',proofs)
    return target


def package_name_group(name):
    return name.split('/')[-2].split('_')[0]
