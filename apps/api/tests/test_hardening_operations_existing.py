"""Focused existing checks, collected without editing their original files."""
import importlib
from test_upload_fallback import uploads  # preserve the imported module's fixture

for module_name in ['test_sqlite_ops','test_logging_config','test_management_pagination','test_characters_api','test_character_assets_api','test_offstage_actor_recall','test_upload_fallback','test_runtime_settings_api','test_model_providers_api']:
    module=importlib.import_module(module_name)
    for name,value in vars(module).items():
        if name.startswith('test_') and callable(value):
            globals()['test_existing_'+module_name+'_'+name]=value
