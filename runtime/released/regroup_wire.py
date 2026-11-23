"""Private wire wrapper for the pinned pure coupon regrouping helper."""
import json
from pathlib import Path
import sys

if sys.argv[1:] not in ([],['--legacy-105'],['--legacy-106'],['--legacy-107']):
    raise SystemExit('unknown regroup worker option')
tree={'--legacy-105':'activity_intake_105','--legacy-106':'activity_intake_106','--legacy-107':'activity_intake'}.get(next(iter(sys.argv[1:]),None),'activity_intake_108')
sys.path.insert(0,str(Path(__file__).resolve().parent/'business_agent'/tree))
from api import IntakeError
from grouping import regroup_coupon_case

MAX_INPUT = 9 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024


def main():
    request_id = None
    code = 'INVALID_INPUT'
    try:
        data = sys.stdin.buffer.read(MAX_INPUT+1)
        if len(data)>MAX_INPUT:
            raise ValueError
        request = json.loads(data)
        request_id = request['requestId']
        if request['contractVersion']!='activity-intake/1.0' or request['operation']!='regroup':
            raise ValueError
        body = request['payload']
        result = regroup_coupon_case(body['analysis'],body['caseId'],body['partitions'],body['limits'],
                                     prior_decisions=body['priorDecisions'])
        wire = {'contractVersion':'activity-intake/1.0','requestId':request_id,'ok':True,'result':result}
        output = json.dumps(wire,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        if len(output)<MAX_OUTPUT:
            sys.stdout.buffer.write(output+b'\n')
            return
        code = 'LIMIT_EXCEEDED'
    except IntakeError as error:
        code = error.code
    except (ValueError, KeyError, TypeError, RecursionError):
        pass
    print(json.dumps({'contractVersion':'activity-intake/1.0','requestId':request_id,'ok':False,'error':{'code':code}}))


if __name__=='__main__':
    main()
