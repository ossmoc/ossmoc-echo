"""Build an empty-data public distribution; no machine-local provisioning."""
from pathlib import Path
import shutil
import subprocess
import sys
from importlib.metadata import distributions


def main():
    root=Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,str(root/'tools/build_portable.py'),'--icon-only'],check=True)
    stage=root/'artifacts/public-build'
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(stage),
                    '--workpath',str(root/'artifacts/pyinstaller-work'),str(root/'Ludiglot.spec')],cwd=root,check=True)
    destination=root/'artifacts/public/OssmocEcho'
    if destination.exists():
        raise FileExistsError('Move your previous public output aside before rebuilding: '+str(destination))
    shutil.copytree(stage/'Ludiglot',destination)
    for name in ('LICENSE','NOTICE.md','THIRD_PARTY.md','SOURCES.md'):
        shutil.copy2(root/name,destination/name)
    shutil.copytree(root/'LICENSES',destination/'LICENSES')
    shutil.copy2(root/'docs/USER-GUIDE.md',destination/'HUONG-DAN.md')
    for distribution in distributions():
        for entry in distribution.files or ():
            if Path(str(entry)).name.upper().startswith(('LICENSE','LICENCE','COPYING','NOTICE','AUTHORS')):
                original=distribution.locate_file(entry)
                if original.is_file():
                    relative=Path(str(entry))
                    if relative.is_absolute() or '..' in relative.parts:
                        continue
                    target=destination/'licenses'/distribution.metadata['Name']/relative
                    target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(original,target)
    print(destination/'OssmocEcho.exe')

if __name__=='__main__': main()
