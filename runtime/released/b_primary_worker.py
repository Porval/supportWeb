"""Primary Agent protocol over the shared bounded subprocess transport."""
from b_model_worker import ModelWorker, SETTINGS_ENV, INPUT_LIMIT
from b_model_recognition import ProviderFailure
from b_state import canon

class PrimaryWorker(ModelWorker):
    contract='activity-primary-recognition/1.0'
    prefix='MODEL_RECOGNITION_'
    manifest_sha256='3af2f97e09bfd51d2e00dfcef751500d739ec391dc4704925d5bc136a92d812d'
    prompt_version='activity-primary-recognition-prompt/1.0.0'
    module='business_agent.activity_primary_recognition.worker'

    def pinned(self):
        from b_model_worker import file_hash
        try:
            manifest=self.directory/'SHA256SUMS'
            if file_hash(manifest)!=self.manifest_sha256:return False
            for line in manifest.read_text().splitlines():
                expected,name=line.split()
                path=self.directory/name
                if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()) or file_hash(path)!=expected:return False
            return file_hash(self.directory.parent/'workbench_deepseek/adapter.py')=='3ad26b221c82193bf301dc70d9c7504f1f4e3740e9c8f9e4f3f837997fa35979'
        except (OSError,ValueError):return False

    def import_root(self):
        return self.directory.parent.parent

    def recognize(self,request,stop,deadline):
        if not self.pinned():raise ProviderFailure('UNAVAILABLE')
        payload=canon({'contractVersion':self.contract,'consent':True,**request}).encode()
        if len(payload)>INPUT_LIMIT:raise ProviderFailure('INPUT_INVALID')
        result=self.invoke([],payload,stop,deadline)
        if result.get('promptVersion')!=self.prompt_version:raise ProviderFailure('INVALID_OUTPUT')
        return result
