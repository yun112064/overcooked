"""Packaged rc1 schemas: no dependency on docs being deployed with Docker."""
import copy
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
VOLUME_EXTENSION = json.loads((ROOT/'volume-extension-v1.json').read_text(encoding='utf-8'))
for name, properties in VOLUME_EXTENSION['schema_properties'].items():
    SCHEMAS['$defs'][name]['properties'].update(properties)
# Keep the shipped rc1 schema and stored content hashes intact. The composed
# contract accepts either package dimension while retaining all price gates.
SCHEMAS['$defs']['PriceContent']['allOf'][0]['then']['properties'].pop('package_mass_g')
SCHEMAS['$defs']['PriceContent']['allOf'].append(VOLUME_EXTENSION['price_package_rule'])
CAPTURE_DRAFT_DEFS = dict(SCHEMAS['$defs'])
for name in ('RecipeContent', 'RecipeLine'):
    CAPTURE_DRAFT_DEFS[name] = copy.deepcopy(CAPTURE_DRAFT_DEFS[name])
capture_recipe = CAPTURE_DRAFT_DEFS['RecipeContent']['properties']
for field in ('category', 'cuisine', 'meal_type'):
    capture_recipe[field]['type'] = ['string', 'null']
capture_recipe['servings']['properties']['basis']['enum'] = ['portions']
capture_recipe['servings']['properties']['raw_text']['type'] = ['string', 'null']
CAPTURE_DRAFT_DEFS['RecipeLine']['properties']['canonical_name']['type'] = ['string', 'null']
RECIPE_CAPTURE_KINDS = ('web_capture', 'ai_output')
POLICY = json.loads((ROOT/'permissions.json').read_text(encoding='utf-8'))
VERSION = POLICY['contract_version']
RULES = {'version':'management-core-v1','contract':VERSION,'external_execution':False,'formal_generation':False,'source_review_required':True,
         'price_quantity_contract': VOLUME_EXTENSION['extension_version']}
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
def validate(name,value,*,definitions=None):
    value=normalize(value)
    validator=Draft202012Validator({'$defs':definitions or SCHEMAS['$defs'],'$ref':'#/$defs/'+name},format_checker=FORMATS)
    if next(validator.iter_errors(value),None) is not None:
        raise ApiError(422,'INVALID_INPUT','輸入不符合 '+VERSION+' 的 '+name+' 格式。')
    if isinstance(value,dict) and 'reason' in value and not value['reason'].strip():
        raise ApiError(422,'INVALID_INPUT','請填寫操作原因。')
    return value
def rules_pin(): return {'rule_set_version':RULES['version'],'rule_set_sha256':digest('rules',RULES)}


def recipe_capture_sources(content, kinds=RECIPE_CAPTURE_KINDS):
    sources = content.get('sources') if isinstance(content, dict) else None
    if not isinstance(sources, list):
        return []
    return [source for source in sources if isinstance(source, dict) and source.get('origin_kind') in kinds]


def icook_sources(content):
    return recipe_capture_sources(content, ('web_capture',))
