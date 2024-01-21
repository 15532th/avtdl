import textwrap
from typing import List, Optional, Type

import markdown
from markdown.extensions.toc import TocExtension
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from core.plugins import Plugins

# implicitly relies on first of the lines inside `description` being a short title
PLUGIN_INFO_TEMPLATE = '''
### `{name}` - {description}
'''

PLUGIN_OPTIONS_TEMPLATE = '''
#### Plugin configuration options:
{config}

'''
ENTITY_OPTIONS_TEMPLATE = '''
#### Entity configuration options:
{entity}
'''

LIST_ITEM_TEMPLATE = '* `{name}`: {description}'

HTML_PAGE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>...</title>
    <link rel="stylesheet" href="modest.css">
  </head>
  <body>
    {body}
  </body>
</html>
'''

ASSOCIATED_RECORDS_TEMPLATE = '''
#### Produced records types:
'''

COLLAPSIBLE_ITEM_TEMPLATE = '''
<details markdown="block">
  <summary>{title}</summary>

{content}

</details>
'''

def get_plugin_info(plugin_name: str) -> str:
    plugin, config, entity = Plugins.get_actor_factories(plugin_name)
    description = render_doc(plugin)
    text = [PLUGIN_INFO_TEMPLATE.format(name=plugin_name, description=description)]
    config_info = get_model_info(config, skip_name=True)
    if config_info:
        text.append(PLUGIN_OPTIONS_TEMPLATE.format(config=config_info))
    entity_info = get_model_info(entity)
    if entity_info:
        text.append(ENTITY_OPTIONS_TEMPLATE.format(entity=entity_info))
    associated_records = Plugins.get_associated_records(plugin_name)
    if associated_records:
        records_text = [ASSOCIATED_RECORDS_TEMPLATE]
        for record_type in associated_records:
            record_info = get_model_info(record_type, skip_details=True)
            record_text = COLLAPSIBLE_ITEM_TEMPLATE.format(title=record_type.__name__, content=record_info)
            records_text.append(record_text)
        text.extend(records_text)
    return '\n'.join(text)


def get_model_info(model: Type[BaseModel], skip_name: bool = False, skip_details=False) -> str:
    info: List[str] = []
    description = render_doc(model)
    if description:
        info.append(description)
        info.append('\n') # ensure newline before list to make it render correctly
    required_fields = []
    not_required_fields = []
    for name, field_info in model.model_fields.items():
        if skip_name and name == 'name':
            continue
        if field_info.exclude:
            continue
        field_description = render_field_info(field_info, skip_details=skip_details)
        field_description_text = LIST_ITEM_TEMPLATE.format(name=name, description=field_description)
        if has_default(field_info):
            not_required_fields.append(field_description_text)
        else:
            required_fields.append(field_description_text)
    if required_fields:
        info.extend(required_fields)
    if not_required_fields:
        content = '\n'.join(not_required_fields)
        spoiler = COLLAPSIBLE_ITEM_TEMPLATE.format(title='non-mandatory fields', content=content)
        info.append(spoiler)
    return '\n'.join(info)


def render_doc(model: Type[BaseModel]) -> str:
    if model.__doc__:
        text = textwrap.dedent(model.__doc__).strip('\n')
        return text
    return ''


def render_field_info(field_info: FieldInfo, skip_details=False) -> str:
    FIELD_INFO_TEMPLATE = '{description}. {details}'
    default = get_default(field_info)
    if skip_details:
        details = ''
    elif default:
        details = f'Default value is `{default}.`'
    else:
        details = 'Required.' if field_info.is_required() else 'Not required.'
    description = field_info.description or ''
    return FIELD_INFO_TEMPLATE.format(details=details, description=description)


def has_default(field_info: FieldInfo) -> bool:
    return field_info.default is not PydanticUndefined

def get_default(field_info: FieldInfo) -> Optional[str]:
    """Return text describing default value of given FieldInfo if set"""
    value = field_info.default
    if value is PydanticUndefined:
        return None
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str): # catches Enum(str)
        if not value.strip(' \t\r\n'):
            return None
        return value
    if isinstance(value, type):
        return value.__name__
    return str(value)


def render_plugins_descriptions() -> str:
    """load available plugins and generate a help file in markdown from docstrings"""
    HELP_FILE_STATIC_PART = '## Description and configuration of available plugins\n[TOC]\n'
    Plugins.load()
    descriptions = {name: get_plugin_info(name) for name in Plugins.known[Plugins.kind.ACTOR].keys()}
    SEPARATOR = '\n---\n'
    text = SEPARATOR.join(['', *descriptions.values(), ''])
    return HELP_FILE_STATIC_PART + text


def render_markdown(text: str) -> str:
    """convert markdown to html fragment"""
    md = markdown.Markdown(extensions=[TocExtension(toc_depth=3), 'md_in_html'])
    html = md.convert(text)
    return html


if __name__ == '__main__':
    Plugins.load()
    text = {name: get_plugin_info(name) for name in Plugins.known[Plugins.kind.ACTOR].keys()}
    text = render_plugins_descriptions()
    html = render_markdown(text)
    html = HTML_PAGE_TEMPLATE.format(body=html)
    ...