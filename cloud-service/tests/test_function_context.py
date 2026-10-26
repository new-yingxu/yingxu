import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import hashlib
module_path=Path(__file__).resolve().parents[2]/'infra/cloud/verify_function_context.py'
spec=importlib.util.spec_from_file_location('verify_function_context',module_path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FunctionContext(unittest.TestCase):
    def fixture(self,root):
        (root/'scf_bootstrap').write_text('#!/bin/sh\nexec python app.py\n')
        (root/'scf_bootstrap').chmod(0o755)
        wheel=root/'third_party/pkg.dist-info/WHEEL'
        wheel.parent.mkdir(parents=True)
        wheel.write_text('Wheel-Version: 1.0\nTag: py3-none-any\n')
        files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        (root/'build-manifest.json').write_text(json.dumps({'appVersion':'fixture','schemaVersion':10,'files':files}))

    def test_modified_or_unlisted_payload_blocks_deployment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.fixture(root)
            self.assertEqual(module.verify_context(root)['filesVerified'],2)
            manifest=json.loads((root/'build-manifest.json').read_text())
            manifest['buildId']='0'*64
            (root/'build-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):module.verify_context(root)
            del manifest['buildId']
            (root/'build-manifest.json').write_text(json.dumps(manifest))
            (root/'unexpected.txt').write_text('extra')
            with self.assertRaises(ValueError):module.verify_context(root)
            (root/'unexpected.txt').unlink()
            (root/'scf_bootstrap').write_text('changed')
            with self.assertRaises(ValueError):module.verify_context(root)

    def test_manifest_cannot_escape_or_include_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'context'
            root.mkdir()
            self.fixture(root)
            credential=Path(tmp)/'credentials.json'
            credential.write_text(json.dumps({'data':{'ApiKey':'synthetic-key'}}))
            (root/'scf_bootstrap').write_text('synthetic-key\n')
            manifest=json.loads((root/'build-manifest.json').read_text())
            manifest['files']['scf_bootstrap']=hashlib.sha256((root/'scf_bootstrap').read_bytes()).hexdigest()
            (root/'build-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):module.verify_context(root,credential)
            (root/'scf_bootstrap').write_text('#!/bin/sh\nexec python app.py\n')
            manifest['files']['scf_bootstrap']=hashlib.sha256((root/'scf_bootstrap').read_bytes()).hexdigest()
            manifest['appVersion']='synthetic-key'
            (root/'build-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):module.verify_context(root,credential)
            manifest['appVersion']='fixture'
            (root/'build-manifest.json').write_text(json.dumps(manifest))
            credential.write_text(json.dumps({'functions':[{'envVariables':{'APP_VERSION':'other'}}]}))
            with self.assertRaises(ValueError):module.verify_context(root,credential)
            manifest['files']={'../credentials.json':'anything'}
            (root/'build-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):module.verify_context(root)
