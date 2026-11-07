"""Packaged rc1 schemas: no dependency on docs being deployed with Docker."""
import hashlib
import json
import unicodedata
from datetime import datetime
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from app.api.errors import ApiError

ROOT = Path(__file__).with_name('contracts')
SCHEMAS = json.loads((ROOT/'schemas.json').read_text(encoding='utf-8'))
PRICE_EXTENSION = json.loads((ROOT/'price-extension-v1.json').read_text(encoding='utf-8'))
for name, properties in PRICE_EXTENSION['schema_properties'].items():
    SCHEMAS['$defs'][name]['properties'].update(properties)
POLICY = json.loads((ROOT/'permissions.json').read_text(encoding='utf-8'))
VERSION = POLICY['contract_version']
RULES = {'version':'management-core-v1','contract':VERSION,'external_execution':False,'formal_generation':False,'source_review_required':True}
def normalize(value):
    if hasattr(value,'_mapping'):return [normalize(v) for v in value]
    if isinstance(value,float): raise ApiError(422,'INVALID_INPUT','浮點數不可作為管理資料。')
    if isinstance(value,str): return unicodedata.normalize('NFC',value)
    if isinstance(value,(list,tuple)): return [normalize(v) for v in value]
    if isinstance(value,dict):
        result={}
        for key,val in value.items():
            key=normalize(key)
            if key in result: raise ApiError(422,'INVALID_INPUT','JSON 欄位名稱重複。')
            result[key]=normalize(val)
        return result
    return value
def canonical(value): return json.dumps(normalize(value),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def digest(kind,value): return hashlib.sha256(('management-'+kind+'-v1\n'+canonical(value)).encode()).hexdigest()
FORMATS=FormatChecker()
@FORMATS.checks('date-time',raises=ValueError)
def date_format(value): return not isinstance(value,str) or datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is not None
def validate(name,value):
    value=normalize(value)
    validator=Draft202012Validator({'$defs':SCHEMAS['$defs'],'$ref':'#/$defs/'+name},format_checker=FORMATS)
    if next(validator.iter_errors(value),None) is not None:
        raise ApiError(422,'INVALID_INPUT','輸入不符合 '+VERSION+' 的 '+name+' 格式。')
    if isinstance(value,dict) and 'reason' in value and not value['reason'].strip():
        raise ApiError(422,'INVALID_INPUT','請填寫操作原因。')
    return value
def rules_pin(): return {'rule_set_version':RULES['version'],'rule_set_sha256':digest('rules',RULES)}
