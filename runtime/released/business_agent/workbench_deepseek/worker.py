"""Private one-shot process for the workbench runner. No shared state or retries."""
import argparse
import os
import sys

if __package__:
    from .adapter import AdapterError,CONTRACT_VERSION,INPUT_INVALID,PROMPT_VERSION,decode,encode,load_private_config,require,suggest
else:
    from adapter import AdapterError,CONTRACT_VERSION,INPUT_INVALID,PROMPT_VERSION,decode,encode,load_private_config,require,suggest

WORKER_VERSION='1.0.0'
SETTINGS_ENV='JIATAN_INTAKE_DEEPSEEK_SETTINGS'
MAX_INPUT_BYTES=256*1024


def handle(raw,load_config,*,transport=None):
    try:
        require(isinstance(raw,bytes) and 0<len(raw)<=MAX_INPUT_BYTES,INPUT_INVALID)
        try:
            payload=decode(raw)
        except AdapterError:
            raise AdapterError(INPUT_INVALID) from None
        require(isinstance(payload,dict)
                and {'contractVersion','consent','excerpts'}<=set(payload)<={'contractVersion','consent','excerpts','candidateAnchors'}
                and payload['contractVersion']==CONTRACT_VERSION and payload['consent'] is True,INPUT_INVALID)
        options={'transport':transport} if transport is not None else {}
        result=suggest(load_config(),payload['excerpts'],candidate_anchors=payload.get('candidateAnchors'),**options)
        return {'contractVersion':CONTRACT_VERSION,'ok':True,'result':result}
    except AdapterError as exc:
        return {'contractVersion':CONTRACT_VERSION,'ok':False,'error':{'code':exc.code}}
    except Exception:
        return {'contractVersion':CONTRACT_VERSION,'ok':False,'error':{'code':'MODEL_RECOGNITION_UNAVAILABLE'}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--status',action='store_true')
    args=parser.parse_args()
    def load_config():
        return load_private_config(os.environ.get(SETTINGS_ENV))
    if args.status:
        try:
            model=load_config().model
        except AdapterError:
            model=None
        result={'contractVersion':CONTRACT_VERSION,'ok':True,
                'result':{'enabled':model is not None,'model':model,'promptVersion':PROMPT_VERSION}}
    else:
        raw=sys.stdin.buffer.read(MAX_INPUT_BYTES+1)
        result=handle(raw,load_config)
    sys.stdout.buffer.write(encode(result)+b'\n')


if __name__=='__main__':
    main()
