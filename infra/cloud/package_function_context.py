"""Build and read back a credential-scanned local ZIP. Never deploys."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from verify_function_context import verify_context

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('context',type=Path)
    parser.add_argument('--credential-file',type=Path,required=True)
    args=parser.parse_args()
    context=args.context.resolve()
    result=verify_context(context,args.credential_file,ROOT)
    manifest=json.loads((context/'build-manifest.json').read_text())
    folder=Path(__file__).resolve().parent/'function-packages'
    folder.mkdir(exist_ok=True)
    package=folder/(result['appVersion']+'-'+result['buildId'][:12]+'.zip')
    if package.exists():raise SystemExit('Refusing to overwrite existing package: '+str(package))
    files=sorted(set(manifest['files'])|{'build-manifest.json'})
    with zipfile.ZipFile(package,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in files:archive.write(context/name,name)
    with zipfile.ZipFile(package) as archive:
        if set(archive.namelist())!=set(files):raise ValueError('archive file set differs')
        for name,expected in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=expected:raise ValueError('archive readback mismatch')
        if not ((archive.getinfo('scf_bootstrap').external_attr>>16)&0o111):raise ValueError('archive bootstrap lost executable bit')
    digest=hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix('.zip.sha256').write_text(digest+'  '+package.name+'\n')
    result.update(package=str(package),sha256=digest,bytes=package.stat().st_size,archiveFiles=len(files),archiveReadback=True)
    package.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
