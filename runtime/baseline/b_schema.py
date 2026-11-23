"""Strict validator for the frozen Agent schemas' small supported vocabulary."""
import json
from pathlib import Path
import re

def check(value,schema):
    def equal(a,b):return type(a) is type(b) and a==b
    if 'const' in schema and not equal(value,schema['const']):raise ValueError('const')
    if 'enum' in schema and not any(equal(value,v) for v in schema['enum']):raise ValueError('enum')
    if 'type' in schema:
        types=schema['type'] if isinstance(schema['type'],list) else [schema['type']]
        matches={'null':value is None,'boolean':type(value) is bool,'integer':type(value) is int,'number':type(value) in (int,float),'string':isinstance(value,str),'array':isinstance(value,list),'object':isinstance(value,dict)}
        if not any(matches.get(t,False) for t in types):raise ValueError('type')
    if isinstance(value,dict):
        props=schema.get('properties',{})
        if set(schema.get('required',[]))-set(value):raise ValueError('required')
        if schema.get('additionalProperties') is False and set(value)-set(props):raise ValueError('unknown')
        for k,v in value.items():
            if k in props:check(v,props[k])
    if isinstance(value,list):
        if len(value)>schema.get('maxItems',len(value)):raise ValueError('items')
        if 'items' in schema:
            for item in value:check(item,schema['items'])
    if isinstance(value,str):
        if len(value)<schema.get('minLength',0) or len(value)>schema.get('maxLength',len(value)):raise ValueError('length')
        if 'pattern' in schema and not re.search(schema['pattern'],value):raise ValueError('pattern')

def validate_agent(value,kind):
    schema=Path(__file__).resolve().parents[2]/'business_agent/activity_intake/schemas'/(kind+'.schema.json')
    check(value,json.loads(schema.read_text()))
