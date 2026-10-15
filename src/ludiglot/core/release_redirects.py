"""Recover only explicitly evidenced legacy MultiText redirects in a release."""
import re
import stat
import zipfile
import unicodedata


_STORY=re.compile(r'^editor/build_support/Wahu/data/translations_v(\d+)_story/01_(?:COT_TRUYEN_CHINH|NPC)_(\d+)\.(\d+)_CHUA_DICH_PART_\d+\.txt$')


def exact_text(value):
    return ' '.join(unicodedata.normalize('NFC',value).split())


def story_redirects(archive,canonical,masked,occupied,check_cancel):
    candidates={}
    ambiguous=set()
    total=0
    seen=set()
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            check_cancel()
            match=_STORY.fullmatch(entry.filename)
            if not match:
                continue
            version=(int(match[2]),int(match[3]))
            if match[1]!=str(version[0])+str(version[1]):
                raise ValueError('Phiên bản nguồn cốt truyện không khớp thư mục.')
            total+=entry.file_size
            if (entry.filename.casefold() in seen or entry.flag_bits&1
                    or stat.S_ISLNK(entry.external_attr>>16)
                    or entry.file_size>32*1024**2 or total>64*1024**2):
                raise ValueError('Nguồn cốt truyện trùng hoặc không an toàn.')
            seen.add(entry.filename.casefold())
            def save(header,fields):
                if not header:
                    return
                source,table,key=header
                source=source.casefold()
                destination=('lang_multi_text.db',key)
                if (source not in {'lang_multi_text_1sthalf.db','lang_multi_text_2ndhalf.db'}
                        or table!='MultiText' or not re.fullmatch(r'(?:Main|Side|GNNPC)_.+_\d+_\d+',key)
                        or destination in occupied or destination in ambiguous
                        or masked.get(destination)!=(version,table)):
                    return
                en,cn,vi=(('\n'.join(fields.get(field,[]))).strip() for field in ('EN','CN','VI'))
                if (not en or '*' in en or not cn or '*' in cn or not vi
                        or not re.search(r'[\u3400-\u9fff]',cn)):
                    return
                target=canonical.get(destination,'')
                # Both siblings carry the ID, sometimes with DIFFERENT targets.
                # The explicit story VI must select this exact main-file target;
                # no target equality, mask-length inference or nearest sibling.
                if not target or exact_text(vi)!=exact_text(target):
                    return
                if not canonical.get((source,key)) or exact_text(canonical.get((source,key),''))==exact_text(target):
                    return  # Cannot use equal targets to infer redirect direction.
                proof=(destination[0],key,source,table,f'{version[0]}.{version[1]}',en,cn,target)
                old=candidates.get(destination)
                if old and old!=proof:
                    candidates.pop(destination,None)
                    ambiguous.add(destination)
                else:
                    candidates[destination]=proof
            header,fields,field=None,{},None
            for line in package.read(entry).decode('utf-8-sig').splitlines():
                check_cancel()
                if line.startswith('@@ '):
                    save(header,fields)
                    parts=line[3:].split('||')
                    header=tuple(parts) if len(parts)==3 and all(parts) else None
                    fields,field={},None
                elif line.startswith('#'):
                    continue
                else:
                    field_match=re.match(r'^(EN|CN|VI|HV|JP): ?(.*)$',line)
                    if field_match:
                        field=field_match[1]
                        if field in fields:
                            header=None
                        fields[field]=[field_match[2]]
                    elif field:
                        fields[field].append(line)
            save(header,fields)
    return tuple(candidates.values())
