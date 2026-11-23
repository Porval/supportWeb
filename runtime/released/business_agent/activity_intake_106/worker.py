"""One request/response on stdin/stdout. Backend owns the overall 10s deadline."""
import base64
import json
import sys
from api import CONTRACT, IntakeError, analyze_intake, decode_material, require, limits_for

MAX_INPUT = 9 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024

def pairs(values):
    result={}
    for k,v in values:
        require(k not in result,'INVALID_JSON')
        result[k]=v
    return result

def reject_constant(value):
    raise IntakeError('INVALID_JSON')

def main():
    request_id=None
    try:
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        require(len(raw)<=MAX_INPUT,'LIMIT_EXCEEDED')
        request=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=reject_constant)
        require(isinstance(request,dict) and set(request)=={'contractVersion','requestId','operation','payload'})
        require(request['contractVersion']==CONTRACT and isinstance(request['requestId'],str) and 0<len(request['requestId'])<=200)
        request_id=request['requestId'];payload=request['payload']
        require(isinstance(payload,dict))
        payload=dict(payload)
        limits=payload.pop('limits',None)
        limits_for(limits)
        if request['operation']=='decode':
            require(set(payload)=={'materialId','filename','mediaType','sha256','contentBase64'})
            require(isinstance(payload['contentBase64'],str))
            data=base64.b64decode(payload['contentBase64'],validate=True)
            material={k:v for k,v in payload.items() if k!='contentBase64'}
            material['contentBytes']=data
            result=decode_material(material,limits)
        elif request['operation']=='analyze': result=analyze_intake(payload,limits)
        else: raise IntakeError('INVALID_OPERATION')
        response={'contractVersion':CONTRACT,'requestId':request_id,'ok':True,'result':result}
    except IntakeError as e:
        response={'contractVersion':CONTRACT,'requestId':request_id,'ok':False,'error':{'code':e.code,'message':e.message,'details':{}}}
    except Exception:
        # Do not echo attacker input, paths, data or stack traces to either stream.
        response={'contractVersion':CONTRACT,'requestId':request_id,'ok':False,'error':{'code':'INVALID_INPUT','message':'Invalid request or material structure','details':{}}}
    encoded=json.dumps(response,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    if len(encoded)>MAX_OUTPUT:
        encoded=json.dumps({'contractVersion':CONTRACT,'requestId':request_id,'ok':False,'error':{'code':'LIMIT_EXCEEDED','message':'Output exceeds 2 MiB','details':{}}},separators=(',',':')).encode()
    sys.stdout.buffer.write(encoded+b'\n')

if __name__=='__main__': main()
