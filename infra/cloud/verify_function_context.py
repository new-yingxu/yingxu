"""Verify a built function context before any deployment; never prints secrets."""
import ast
import argparse
import hashlib
import json
from pathlib import Path


def verify_context(output,credential_file=None,source_root=None):
    output=Path(output).resolve()
    manifest_path=output/'build-manifest.json'
    if not manifest_path.resolve().is_relative_to(output):raise ValueError('manifest escapes context')
    manifest=json.loads(manifest_path.read_text())
    files=manifest.get('files')
    if not isinstance(files,dict) or not files:raise ValueError('missing file manifest')
    if manifest.get('buildId') and manifest['buildId']!=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest():raise ValueError('build identity mismatch')
    actual={str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()}
    if actual != set(files)|{'build-manifest.json'}:raise ValueError('unlisted or missing context files')
    for name,expected in files.items():
        path=(output/name).resolve()
        if not path.is_relative_to(output):raise ValueError('manifest path escapes context')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('file fingerprint mismatch: '+name)
        if path.name.startswith('.env'):raise ValueError('environment file in deployment context')
    bootstrap=output/'scf_bootstrap'
    if not bootstrap.stat().st_mode & 0o111:raise ValueError('bootstrap is not executable')
    if b'\r\n' in bootstrap.read_bytes():raise ValueError('bootstrap must use LF line endings')
    packages=list((output/'third_party').glob('*.dist-info/WHEEL'))
    if not packages:raise ValueError('runtime dependencies missing')
    for package in packages:
        text=package.read_text()
        if 'macosx' in text or 'win_' in text:raise ValueError('non-Linux dependency in context')
    if credential_file:
        credential_path=Path(credential_file).resolve()
        if credential_path.is_relative_to(output):raise ValueError('credential configuration must stay outside context')
        config=json.loads(credential_path.read_text())
        secrets=[]
        if isinstance(config.get('data'),dict):
            key=config['data'].get('ApiKey')
            if key:secrets.append(key.encode())
        for function in config.get('functions',[]):
            env=function.get('envVariables',{})
            if env.get('APP_VERSION') and env['APP_VERSION']!=manifest['appVersion']:raise ValueError('deployment config APP_VERSION differs from bundle')
            for name,value in env.items():
                if any(part in name.upper() for part in ('KEY','TOKEN','SECRET','CREDENTIAL')) and isinstance(value,str) and value:
                    secrets.append(value.encode())
                    if name=='MODEL_OWNER_CREDENTIALS':
                        for owner in json.loads(value).values():
                            if isinstance(owner,dict) and owner.get('apiKey'):secrets.append(owner['apiKey'].encode())
        for name in actual:
            body=(output/name).read_bytes()
            if any(secret in body for secret in secrets):raise ValueError('credential bytes detected in deployment context')
    if source_root:
        source=Path(source_root).resolve()
        tree=ast.parse((source/'cloud-service/app/config.py').read_text())
        version=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='DEFAULT_APP_VERSION' for target in node.targets))
        schema=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='REQUIRED_SCHEMA_VERSION' for target in node.targets))
        if schema!=manifest['schemaVersion']:raise ValueError('bundle schemaVersion differs from source')
        if version!=manifest['appVersion']:raise ValueError('bundle appVersion differs from source')
        expected={}
        for folder in ('cloud-service/app','contracts'):
            for path in (source/folder).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
                    relative=str(path.relative_to(source/folder))
                    target=('app/' if folder.endswith('/app') else 'contracts/')+relative
                    expected[target]=hashlib.sha256(path.read_bytes()).hexdigest()
        packaged={k:v for k,v in files.items() if k.startswith(('app/','contracts/'))}
        if not expected or expected!=packaged:raise ValueError('context does not match current source/contracts')
        lock=hashlib.sha256((source/'cloud-service/requirements.linux.lock').read_bytes()).hexdigest()
        if files.get('requirements.lock')!=lock or files.get('requirements.txt')!=lock:raise ValueError('context dependency lock differs from source')
        if files.get('scf_bootstrap')!=hashlib.sha256((source/'infra/cloud/scf_bootstrap').read_bytes()).hexdigest():raise ValueError('context bootstrap differs from source')
    return {'buildId':manifest.get('buildId'),'appVersion':manifest['appVersion'],'schemaVersion':manifest['schemaVersion'],'filesVerified':len(files),'packagesVerified':len(packages),'sourceMatched':bool(source_root),'credentialsChecked':bool(credential_file),'deployed':False}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('context',type=Path)
    parser.add_argument('--credential-file',type=Path)
    parser.add_argument('--source-root',type=Path)
    args=parser.parse_args()
    print(json.dumps(verify_context(args.context,args.credential_file,args.source_root),ensure_ascii=False))


if __name__=='__main__':main()
