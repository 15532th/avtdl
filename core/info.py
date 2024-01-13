import textwrap
from typing import List, Optional, Type

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from core.plugins import Plugins

PLUGIN_INFO_TEMPLATE = '''
### {name}
{description}

#### Plugin configuration
{config}

#### Entity configuration
{entity}
'''


def get_plugin_info(plugin_name: str) -> str:
    plugin, config, entity = Plugins.get_actor_factories(plugin_name)
    description = render_doc(plugin)
    config_info = get_model_info(config, skip_name=True) if issubclass(config, BaseModel) else "Plugin has no configuration options"
    entity_info = get_model_info(entity) if issubclass(entity, BaseModel) else "Plugin entities has no configuration options"
    text = PLUGIN_INFO_TEMPLATE.format(name=plugin_name, description=description, config=config_info, entity=entity_info)
    return text.strip('\n')


def get_model_info(model: Type[BaseModel], skip_name: bool = False) -> str:
    LIST_ITEM_TEMPLATE = '- `{name}`: {description}'

    info: List[str] = []
    description = render_doc(model)
    if description:
        info.append(description)
    for name, field_info in model.model_fields.items():
        if skip_name and name == 'name':
            continue
        if field_info.exclude:
            continue
        field_description = render_field_info(field_info)
        info.append(LIST_ITEM_TEMPLATE.format(name=name, description=field_description))
    return '\n'.join(info)


def render_doc(model: Type[BaseModel]) -> str:
    text = ''
    if model.__doc__:
        text = model.__doc__
    elif isinstance(model, type):
        for predecessor in model.mro():
            if predecessor.__doc__ and predecessor is not BaseModel:
                text = predecessor.__doc__
                break
            if predecessor is BaseModel:
                break
    return textwrap.dedent(text)


def render_field_info(field_info: FieldInfo) -> str:
    FIELD_INFO_TEMPLATE = '{details}. {description}'
    default = get_default(field_info)
    if default:
        details = f'default value is "{default}"'
    else:
        details = 'required' if field_info.is_required() else 'not required'
    description = field_info.description or ''
    return FIELD_INFO_TEMPLATE.format(details=details, description=description)


def get_default(field_info: FieldInfo) -> Optional[str]:
    """Return text describing default value of given FieldInfo if set"""
    if field_info.default is PydanticUndefined:
        return None
    if field_info.default is None:
        return None
    if isinstance(field_info.default, bool):
        return str(field_info.default).lower()
    if isinstance(field_info.default, type):
        return field_info.default.__name__
    return str(field_info.default)


if __name__ == '__main__':
    Plugins.load()
    text = {name: get_plugin_info(name) for name in Plugins.known[Plugins.kind.ACTOR].keys()}
    ...