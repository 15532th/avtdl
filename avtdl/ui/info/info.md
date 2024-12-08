## avtdl - Web interface

When avtdl is running, web interface is available on <http://localhost:8080>, unless different port is specified in
configuration. It makes configuration process more grafical at the expense of flexibility YAML provides.

### Configuration editor

Web interface follows [configuration file structure](https://github.com/15532th/avtdl#configuration-file-format), with
application-wide options in `Settings`, producers and consumers entities defined in `Actors` and then combined together
in `Chains` sections.

<p align="center" width="100%">  
<figure>
<img src="main_view.png" width="800" alt="Interface of the configuration editor"/>
</figure>
</p>

Configuration file is saved under the same name it was loaded on the application startup. Backups of a few most recent
versions are made by appending a number to the name (`config.yml` would be moved to `config.yml.0` and so on).

When existing configuration file is loaded into the web interface, edited and stored back on disk, it will preserve defined functionality, but not formatting. Parser tries to preserve comments, parenthesses, flow style, but indentation levels are normalized, options with default values are omitted, the `defaults` sections are expanded into corresponding entities and then removed.

### Actors

Individual plugins that produce, filter or process, and consume records are grouped by type into `Monitors`, `Filters` and `Actions` subsections.

Entities are represented as a set of input fields grouped together:

<p align="center" width="100%">  
<figure>
<img src="monitor_rss.png" width="800"/>
</figure>
</p>


Each option has a tooltip with option description, however, it does not include details, described in the [common options](https://github.com/15532th/avtdl#common-options) section of the README.

### Chains

Single chain is represented by a vertical stack of `cards`, each defining one or more entities of a single actor. Records move top to bottom, from every entity of one `card` to every entity of the next one. 

The following picture shows a chain named "archive". It references two entities of the [rss](https://github.com/15532th/avtdl/blob/master/PLUGINS.md#rss---youtube-channel-rss-feed-monitor) montitor, "ChannelName" and "AnotherChannelName". Records, produced by these entities, are passed into the "ytachive" entity of the [execute](https://github.com/15532th/avtdl/blob/master/PLUGINS.md#execute---run-pre-defined-shell-command) action.

<p align="center" width="100%">
<figure>
<img alt="Chain example" src="chain_archive.png" width="250"/>
</figure>
</p>

Corresponding fragment of the YAML config:

```yaml
chains:
  "archive":
    - rss:
        - "ChannelName"
        - "AnotherChannelName"
    - execute:
        - ytarchive
```

Drop-down selectors with entities names are populated with entities names that are currently present in the `Actors` section. When entity is renamed, the change is reflected in all chains referencing it, and when entity is deleted, it gets removed from all chains.

For convenience, it is possible to reorder cards within a chain by using arrow buttons (⇧ and ⇩). Chains themselves can also be repositioned (with ⇦ and ⇨), though their order is purely cosmetic. "Go to entity definition" button (`⤴`) allows jumping from a chain using entity to the entity configuration in `Actors` section.
