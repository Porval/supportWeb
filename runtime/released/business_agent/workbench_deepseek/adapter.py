"""One bounded DeepSeek suggestion request for already-authorized excerpts."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import socket
import stat
from typing import Callable
from urllib import error, request

CONTRACT_VERSION = 'activity-model-recognition/1.0'
PROMPT_VERSION = 'activity-model-recognition-prompt/1.0.0'
ENDPOINT = 'https://api.deepseek.com/chat/completions'
HTTP_TIMEOUT_SECONDS = 50
ATTEMPT_DEADLINE_SECONDS = 60  # Enforced by the caller around this one-shot adapter.
MAX_EXCERPTS = 48
MAX_EXCERPT_CHARS = 800
MAX_EXCERPT_BYTES = 24000
MAX_REQUEST_BYTES = 48 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_TOKENS = 4096
MAX_GROUPS = 20
FIELD_LENGTHS = {'activityName':200, 'platformCode':200, 'sourceActivityId':200,
                 'startAt':200, 'endAt':200, 'rulesText':10000, 'brandBenefit':5000, 'brandNameText':200}
PLATFORMS = {'meituan','taobao_instant','jiatan'}
SOURCE_KINDS = {'text','csv','xlsx','docx'}
EXCERPT_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
CANDIDATE_ID = re.compile(r'candidate-(?:[1-9]|1[0-9]|20)')
MODEL_ID = re.compile(r'deepseek-[A-Za-z0-9_-]{1,80}')
INPUT_INVALID = 'MODEL_RECOGNITION_INPUT_INVALID'
INVALID_OUTPUT = 'MODEL_RECOGNITION_INVALID_OUTPUT'


class AdapterError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str = field(repr=False)
    model: str = 'deepseek-flash'


def load_private_config(settings_file):
    """Read the launcher-selected file after checking the open descriptors."""
    unavailable='MODEL_RECOGNITION_UNAVAILABLE'
    try:
        path=Path(settings_file)
        require(path.is_absolute(),unavailable)
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            info=os.fstat(parent)
            require(info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o700,unavailable)
            fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
                        and stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=8192,unavailable)
                raw=stream.read(8193)
        finally:
            os.close(parent)
        require(len(raw)<=8192,unavailable)
        settings=decode(raw)
        require(isinstance(settings,dict),unavailable)
        return checked_config(DeepSeekConfig(settings.get('key',''),settings.get('model') or 'deepseek-flash'))
    except (OSError,AdapterError,ValueError,TypeError):
        raise AdapterError(unavailable) from None


def checked_config(config):
    if (not isinstance(config,DeepSeekConfig) or not isinstance(config.api_key,str)
            or not re.fullmatch(r'sk-[A-Za-z0-9]{20,100}',config.api_key)
            or not isinstance(config.model,str) or not MODEL_ID.fullmatch(config.model)):
        raise AdapterError('MODEL_RECOGNITION_UNAVAILABLE')
    return config


def require(condition, code):
    if not condition:
        raise AdapterError(code)


def encode(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')


def decode(raw):
    def unique(pairs):
        output={}
        for key,value in pairs:
            require(key not in output,INVALID_OUTPUT)
            output[key]=value
        return output

    def invalid_constant(_value):
        raise AdapterError(INVALID_OUTPUT)

    try:
        return json.loads(raw,object_pairs_hook=unique,parse_constant=invalid_constant)
    except (TypeError,UnicodeError,ValueError,RecursionError):
        raise AdapterError(INVALID_OUTPUT) from None


def prepare_excerpts(excerpts,candidate_anchors):
    """Select the provider-visible fields and build the local reference index."""
    require(isinstance(excerpts,list) and 0<len(excerpts)<=MAX_EXCERPTS,INPUT_INVALID)
    texts={}
    text_bytes=0
    for item in excerpts:
        require(isinstance(item,dict) and set(item)=={'excerptId','materialId','locator','text'},INPUT_INVALID)
        excerpt_id,text=item['excerptId'],item['text']
        require(isinstance(excerpt_id,str) and EXCERPT_ID.fullmatch(excerpt_id) and excerpt_id not in texts,INPUT_INVALID)
        locator=item['locator']
        require(isinstance(item['materialId'],str) and bool(item['materialId'])
                and not item['materialId'].startswith('FORM-') and isinstance(locator,dict)
                and isinstance(locator.get('kind'),str) and locator['kind'] in SOURCE_KINDS,INPUT_INVALID)
        require(isinstance(text,str) and bool(text.strip()) and len(text)<=MAX_EXCERPT_CHARS,INPUT_INVALID)
        try:
            text_bytes+=len(text.encode('utf-8'))
        except UnicodeError:
            raise AdapterError(INPUT_INVALID) from None
        texts[excerpt_id]=text
    require(text_bytes<=MAX_EXCERPT_BYTES,INPUT_INVALID)
    require(candidate_anchors is None or isinstance(candidate_anchors,dict),INPUT_INVALID)
    anchors=candidate_anchors or {}
    require(len(anchors)<=MAX_GROUPS,INPUT_INVALID)
    by_excerpt={}
    for candidate_id,ids in anchors.items():
        require(isinstance(candidate_id,str) and CANDIDATE_ID.fullmatch(candidate_id),INPUT_INVALID)
        require(isinstance(ids,list) and 0<len(ids)<=MAX_EXCERPTS,INPUT_INVALID)
        for excerpt_id in ids:
            require(isinstance(excerpt_id,str) and excerpt_id in texts and excerpt_id not in by_excerpt,INPUT_INVALID)
            by_excerpt[excerpt_id]=candidate_id
    outbound=[]
    for excerpt_id,text in texts.items():
        item={'excerptId':excerpt_id,'text':text}
        if excerpt_id in by_excerpt:
            item['candidateId']=by_excerpt[excerpt_id]
        outbound.append(item)
    return outbound,texts,by_excerpt


def validate_groups(output,texts,by_excerpt):
    require(isinstance(output,dict) and set(output)=={'groups'},INVALID_OUTPUT)
    groups=output['groups']
    require(isinstance(groups,list) and len(groups)<=MAX_GROUPS,INVALID_OUTPUT)
    known_candidates=set(by_excerpt.values())
    seen_candidates=set()
    for group in groups:
        require(isinstance(group,dict) and set(group)=={'candidateId','fields'},INVALID_OUTPUT)
        candidate_id,fields=group['candidateId'],group['fields']
        require(candidate_id is None or (isinstance(candidate_id,str) and candidate_id in known_candidates
                and candidate_id not in seen_candidates),INVALID_OUTPUT)
        if candidate_id is not None:
            seen_candidates.add(candidate_id)
        require(isinstance(fields,list) and len(fields)<=len(FIELD_LENGTHS),INVALID_OUTPUT)
        seen_fields=set()
        group_anchors=set()
        for suggestion in fields:
            require(isinstance(suggestion,dict) and set(suggestion)=={'field','value','refs'},INVALID_OUTPUT)
            name,value,refs=suggestion['field'],suggestion['value'],suggestion['refs']
            require(isinstance(name,str) and name in FIELD_LENGTHS and name not in seen_fields,INVALID_OUTPUT)
            seen_fields.add(name)
            require(value is None or (isinstance(value,str) and value.strip() and len(value)<=FIELD_LENGTHS[name]),INVALID_OUTPUT)
            if value is not None:
                try:
                    value.encode('utf-8')
                except UnicodeError:
                    raise AdapterError(INVALID_OUTPUT) from None
            if name=='platformCode' and value is not None:
                require(value in PLATFORMS,INVALID_OUTPUT)
            require(isinstance(refs,list) and len(refs)<=MAX_EXCERPTS and (value is None or refs),INVALID_OUTPUT)
            seen_refs=set()
            for ref in refs:
                require(isinstance(ref,dict) and set(ref)=={'excerptId','quote'},INVALID_OUTPUT)
                excerpt_id,quote=ref['excerptId'],ref['quote']
                require(isinstance(excerpt_id,str) and excerpt_id in texts,INVALID_OUTPUT)
                require(isinstance(quote,str) and quote.strip() and len(quote)<=500
                        and quote in texts[excerpt_id] and (excerpt_id,quote) not in seen_refs,INVALID_OUTPUT)
                seen_refs.add((excerpt_id,quote))
                anchor=by_excerpt.get(excerpt_id)
                if anchor is not None:
                    group_anchors.add(anchor)
                if candidate_id is not None:
                    require(anchor==candidate_id,INVALID_OUTPUT)
            if name=='brandNameText' and value is not None:
                require(any(value in ref['quote'] for ref in refs),INVALID_OUTPUT)
            if name=='sourceActivityId' and value is not None and re.fullmatch(r'[0-9]+',value):
                token=re.compile(r'(?<![A-Za-z0-9_-])'+re.escape(value)+r'(?![A-Za-z0-9_-])')
                require(any(token.search(ref['quote']) for ref in refs),INVALID_OUTPUT)
        require(len(group_anchors)<=1,INVALID_OUTPUT)
    return groups


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def provider_call(req, timeout):
    """Use a fixed destination and reject redirects, including same-host ones."""
    opener=request.build_opener(request.ProxyHandler({}),NoRedirect())
    with opener.open(req,timeout=timeout) as response:
        return response.read(MAX_RESPONSE_BYTES)


def usage_metadata(body):
    source=body.get('usage')
    if not isinstance(source,dict):
        return {}
    meta={}
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        value=source.get(key)
        if type(value) is int and value>=0:
            meta[key]=value
    detail=source.get('completion_tokens_details')
    if isinstance(detail,dict):
        value=detail.get('reasoning_tokens')
        if type(value) is int and value>=0:
            meta['reasoning_tokens']=value
    return meta


def suggest(config,excerpts,*,candidate_anchors=None,transport:Callable=provider_call,usage_observer=None):
    """Return validated provider groups and safe metadata. The caller binds evidence."""
    config=checked_config(config)
    outbound,texts,by_excerpt=prepare_excerpts(excerpts,candidate_anchors)
    prompt=Path(__file__).with_name('prompt-v1.txt').read_text(encoding='utf-8')
    content=encode({'excerpts':outbound}).decode('utf-8')
    data=encode({'model':config.model,'messages':[{'role':'system','content':prompt},
        {'role':'user','content':content}], 'response_format':{'type':'json_object'},
        'max_tokens':MAX_OUTPUT_TOKENS,'stream':False})
    require(len(data)<=MAX_REQUEST_BYTES,INPUT_INVALID)
    outbound_request=request.Request(ENDPOINT,data=data,method='POST',
        headers={'Authorization':'Bearer '+config.api_key,'Content-Type':'application/json'})
    try:
        raw=transport(outbound_request,HTTP_TIMEOUT_SECONDS)
    except error.HTTPError as exc:
        code={401:'MODEL_RECOGNITION_PROVIDER_AUTH',403:'MODEL_RECOGNITION_PROVIDER_AUTH',
              402:'MODEL_RECOGNITION_PROVIDER_QUOTA',429:'MODEL_RECOGNITION_PROVIDER_QUOTA',
              408:'MODEL_RECOGNITION_TIMEOUT',504:'MODEL_RECOGNITION_TIMEOUT'}.get(
                exc.code,'MODEL_RECOGNITION_PROVIDER_UNAVAILABLE')
        exc.close()
        raise AdapterError(code) from None
    except (TimeoutError,socket.timeout):
        raise AdapterError('MODEL_RECOGNITION_TIMEOUT') from None
    except error.URLError as exc:
        code='MODEL_RECOGNITION_TIMEOUT' if isinstance(exc.reason,TimeoutError) else 'MODEL_RECOGNITION_PROVIDER_UNAVAILABLE'
        raise AdapterError(code) from None
    except OSError:
        raise AdapterError('MODEL_RECOGNITION_PROVIDER_UNAVAILABLE') from None
    require(isinstance(raw,bytes) and 0<len(raw)<MAX_RESPONSE_BYTES,INVALID_OUTPUT)
    body=decode(raw)
    require(isinstance(body,dict),INVALID_OUTPUT)
    choices=body.get('choices')
    require(isinstance(choices,list) and len(choices)==1 and isinstance(choices[0],dict),INVALID_OUTPUT)
    choice=choices[0]
    message=choice.get('message')
    require(choice.get('finish_reason')=='stop' and isinstance(message,dict) and not message.get('tool_calls')
            and not message.get('function_call')
            and isinstance(message.get('content'),str) and bool(message['content'].strip()),INVALID_OUTPUT)
    returned_model=body.get('model')
    require(isinstance(returned_model,str) and MODEL_ID.fullmatch(returned_model),INVALID_OUTPUT)
    groups=validate_groups(decode(message['content']),texts,by_excerpt)
    usage=usage_metadata(body)
    reasoning=message.get('reasoning_content')
    thinking='enabled' if (isinstance(reasoning,str) and reasoning.strip()) or usage.get('reasoning_tokens',0)>0 else 'unknown'
    if usage_observer is not None:
        usage_observer(usage)
    return {'groups':groups,'model':returned_model,'thinking':thinking,'promptVersion':PROMPT_VERSION}
