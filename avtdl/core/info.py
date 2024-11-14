import datetime
import re
import textwrap
from enum import Enum
from typing import Dict, List, Optional, Type, Union

import markdown
from markdown.extensions.toc import TocExtension
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from avtdl.core.interfaces import Action, Filter, Monitor
from avtdl.core.plugins import Plugins

# plugins names starting with this are excluded from docs
INTERNAL_PLUGINS_PATTERN = 'utils'

HELP_FILE_STATIC_PART = f'''
<!-- This file is autogenerated as part of avtdl at {datetime.datetime.utcnow()} -->

## Description and configuration of available plugins

This page provides details on format and possible values of configuration
options of the plugins and plugins' entities, as well as description of
records produced by them.

High-level overview of the configuration file structure can be found in
[README](README.md#configuration-file-format). Example configurations
for common workflows are available [here](EXAMPLES.md).

In each plugin section, *Plugin configuration options*, if present, describes
plugin-wide settings, placed under the `config` part inside the plugin config.
The *Entity configuration options* lists settings for each entity inside the
`entities` list.

A few common options used by multiple plugins, most notable the templating rules,
are described in greater details in the [Common options](README.md#common-options)
section of the README.

### Table of content:
<!-- [TOC] -->

{{TOC}}

<!-- [TOC] -->
---
'''


STRUCTURE = '''
## Monitors:

{}

## Filters:

{}

## Actions:

{}
'''

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
    config_info = get_config_model_info(config)
    if config_info:
        text.append(PLUGIN_OPTIONS_TEMPLATE.format(config=config_info))
    entity_info = get_entity_model_info(entity)
    if entity_info:
        text.append(ENTITY_OPTIONS_TEMPLATE.format(entity=entity_info))
    associated_records = Plugins.get_associated_records(plugin_name)
    if associated_records:
        records_text = [ASSOCIATED_RECORDS_TEMPLATE]
        for record_type in associated_records:
            record_info = get_record_model_info(record_type)
            record_text = COLLAPSIBLE_ITEM_TEMPLATE.format(title=record_type.__name__, content=record_info)
            records_text.append(record_text)
        text.extend(records_text)
    return '\n'.join(text)


def get_model_info(model: Type[BaseModel], skip_name: bool = False) -> str:
    info: List[str] = []
    description = render_doc(model)
    if description:
        info.append(description)
        info.append('\n')  # ensure newline before list to make it render correctly
    required_fields = []
    not_required_fields = []
    for name, field_info in model.model_fields.items():
        if skip_name and name == 'name':
            continue
        if field_info.exclude:
            continue
        field_description = render_field_info(field_info, skip_details=False)
        field_description_text = LIST_ITEM_TEMPLATE.format(name=name, description=field_description)
        if has_default(field_info):
            not_required_fields.append(field_description_text)
        else:
            required_fields.append(field_description_text)
    info.extend(required_fields)
    if required_fields and not_required_fields:
        info.append('##### ')  # separate mandatory and non-mandatory fields in two lists
    info.extend(not_required_fields)
    return '\n'.join(info)


def get_config_model_info(model: Type[BaseModel]) -> str:
    return get_model_info(model, skip_name=True)


def get_entity_model_info(model: Type[BaseModel]) -> str:
    return get_model_info(model, skip_name=False)


def get_record_model_info(model: Type[BaseModel]) -> str:
    info: List[str] = []
    description = render_doc(model)
    if description:
        info.append(description)
        info.append('\n')  # ensure newline before list to make it render correctly
    for name, field_info in model.model_fields.items():
        if field_info.exclude:
            continue
        field_description = render_field_info(field_info, skip_details=True)
        field_description_text = LIST_ITEM_TEMPLATE.format(name=name, description=field_description)
        info.append(field_description_text)
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
        details = f'Default value is `{default}`.'
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
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        if not value.strip(' \t\r\n'):
            return None
        return value
    if isinstance(value, dict):
        return ', '.join(f'"{k}": "{v}"' for k, v in value.items())
    if isinstance(value, type):
        return value.__name__
    return str(value)


def get_plugin_type(name: str) -> Optional[str]:
    plugin = Plugins.known[Plugins.kind.ACTOR].get(name)
    if plugin is None:
        return None
    if name.startswith(INTERNAL_PLUGINS_PATTERN):
        return 'Utils'
    if issubclass(plugin, Monitor):
        return 'Monitors'
    if issubclass(plugin, Filter):
        return 'Filters'
    if issubclass(plugin, Action):
        return 'Actions'
    return None


def get_plugins_descriptions(plugin_type: type = object):
    descriptions = {}
    for name, constructor in Plugins.known[Plugins.kind.ACTOR].items():
        if name.startswith(INTERNAL_PLUGINS_PATTERN):
            continue
        if not issubclass(constructor, plugin_type):
            continue
        descriptions[name] = get_plugin_info(name)
    return descriptions


def render_plugins_descriptions() -> str:
    """load available plugins and generate a help file in markdown from docstrings"""
    SEPARATOR = '\n---\n'

    Plugins.load()
    monitors = get_plugins_descriptions(Monitor)
    filters = get_plugins_descriptions(Filter)
    actions = get_plugins_descriptions(Action)
    descriptions = [SEPARATOR.join(section.values()) for section in [monitors, filters, actions]]
    text = STRUCTURE.format(*descriptions)
    return text


md = markdown.Markdown(extensions=[TocExtension(toc_depth=3), 'md_in_html'])


def render_markdown(text: str) -> str:
    """convert markdown to html fragment"""
    html = md.convert(text)
    return html


def generate_plugins_description(as_html: bool = False):
    text = render_plugins_descriptions()
    full_text = HELP_FILE_STATIC_PART + text
    full_text = TOC.insert_toc(text, full_text)
    if not as_html:
        return full_text
    html = render_markdown(full_text)
    html = HTML_PAGE_TEMPLATE.format(body=html)
    return html


def generate_version_string() -> str:
    try:
        from avtdl._version import __version__
    except ModuleNotFoundError:
        __version__ = '[unknown version]'
    Plugins.load()
    version = f'avtdl {__version__} with plugins:'
    known_plugins = ', '.join([name for name in Plugins.known[Plugins.kind.ACTOR].keys()])
    known_plugins = textwrap.fill(known_plugins, initial_indent='    ', subsequent_indent='    ')
    text = version + '\n' + known_plugins
    return text


class TOCToken(BaseModel):
    name: str
    id: str
    level: int
    children: List['TOCToken']


class TOC:

    @staticmethod
    def slugify(text: str, separator='-') -> str:
        text = re.sub(r'[^\w\s_-]', '', text).strip().lower()
        text = re.sub(r'[\s]+', separator, text)
        return text

    @classmethod
    def generate_toc_tokens(cls, text: str, toc_depth: Union[int, str]) -> List[TOCToken]:
        md = markdown.Markdown(output_format='html', extensions=['md_in_html', TocExtension(toc_depth=toc_depth, slugify=cls.slugify)])
        _ = md.convert(text)
        tokens = [TOCToken.model_validate(token) for token in md.toc_tokens]
        return tokens

    @staticmethod
    def generate_toc_line(t: TOCToken, level: int = 0, indent: str = '  ') -> str:
        line = f'{level * indent}* [{t.name}](#{t.id})'
        return line

    @classmethod
    def generate_toc_md(cls, tokens: List[TOCToken], level: int = 0, indent: str = '  ') -> List[str]:
        md: List[str] = []
        for token in tokens:
            line = cls.generate_toc_line(token, level, indent)
            md.append(line)
            children_md = cls.generate_toc_md(token.children, level + 1, indent)
            md.extend(children_md)
        return md

    @classmethod
    def generate_toc(cls, text: str, toc_depth: Union[int, str], indent: str = '  ') -> str:
        tokens = cls.generate_toc_tokens(text, toc_depth)
        md_lines = cls.generate_toc_md(tokens, indent=indent)
        md = '\n'.join(md_lines)
        return md

    @classmethod
    def insert_toc(cls, source_text: str, target_text: str, toc_marker: str = '{TOC}', toc_depth='2-3', indent: str = '  ') -> str:
       toc = cls.generate_toc(source_text, toc_depth, indent)
       result = target_text.replace(toc_marker, toc)
       return result


def get_known_plugins() -> List[str]:
    plugins_by_type: Dict[str, List[str]] = {
        'Monitors': [],
        'Filters': [],
        'Actions': [],
        'Other': []
    }
    for name, plugin in Plugins.known[Plugins.kind.ACTOR].items():
        plugin_type = get_plugin_type(name)
        if plugin_type not in plugins_by_type:
            plugin_type = 'Other'
        plugins_by_type[plugin_type].append(name)
    plugins = []
    for plugin_list in plugins_by_type.values():
        plugins.extend(plugin_list)
    return plugins
