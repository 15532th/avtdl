class EntitiesList {
    constructor(schema, container = null) {
        this.schema = schema;
        this.entries = [];
        this.container = container || document.createElement('div');
        this.container.classList.add('editable-list');

        this.addButton = createButton('[Add]', () => this.addEntry(), 'add-button');
        this.container.appendChild(this.addButton);
    }

    isEmpty() {
        return this.entries.length == 0;
    }

    addEntry(data = null) {
        const entryDiv = document.createElement('div');
        entryDiv.classList.add('entry-container');
        this.container.insertBefore(entryDiv, this.addButton);

        const entity = new Fieldset(this.schema);
        if (data) {
            entity.fill(data);
        }
        this.entries.push(entity);
        entryDiv.appendChild(entity.getElement());

        const deleteButton = createButton('[×]', () => this.deleteEntry(entity, entryDiv), 'delete-entry');
        entryDiv.appendChild(deleteButton);

        entity.registerNameChangeCallback((newName, nameField) => {
            this.handleNameUpdate(newName, nameField);
        });
    }

    deleteEntry(entry, entryDiv) {
        this.container.removeChild(entryDiv);
        this.entries = this.entries.filter((x) => x !== entry);
    }

    getElement() {
        return this.container;
    }

    listEntries() {
        const names = [];
        for (const entry of this.entries) {
            names.push(entry.getName());
        }
        return names;
    }

    handleNameUpdate(name, nameField) {
        const sameNameEntries = [];
        for (const entry of this.entries) {
            entry.showError(['name'], ''); // clear error by setting empty error message
            if (name == entry.getName()) {
                sameNameEntries.push(entry);
            }
        }
        if (sameNameEntries.length > 1) {
            sameNameEntries.forEach((entry) => {
                entry.showError(['name'], 'name used more than once');
            });
        }
    }

    getEntry(entryName) {
        for (const entry of this.entries) {
            if (entryName == entry.getName()) {
                return entry;
            }
        }
        return null;
    }

    read() {
        const data = [];
        for (const entry of this.entries) {
            data.push(entry.read());
        }
        return data;
    }

    showError(path, message) {
        if (path instanceof Array) {
            if (path.length > 1) {
                const index = path[0];
                if (Number.isInteger(index)) {
                    if (this.entries.length >= index) {
                        return this.entries[index].showError(path.slice(1), message);
                    }
                }
            }
        }
    }
}

class ActorSection {
    constructor(name, data, info, type, configSchema, entitiesSchema) {
        this.name = name;
        this.info = info;
        this.type = type;

        this.configSchema = configSchema;
        this.entitiesSchema = entitiesSchema;

        delete this.configSchema.name;
        delete this.configSchema.defaults;

        let headline;
        [name, headline, info] = this.extendName(name, info);
        this.headline = headline;

        this.container = createDetails(name, info, headline);
        this.container.className = 'actor';

        this.config = this.generateConfig(data);
        this.entities = this.generateEntities(data);
    }

    extendName(name, description) {
        let tempDiv = document.createElement('div');
        tempDiv.innerHTML = description;

        let headline = '';
        let firstParagraphElement = tempDiv.querySelector('p');
        if (firstParagraphElement) {
            headline = firstParagraphElement.innerText;
            firstParagraphElement.remove();
        }
        description = tempDiv.innerHTML;

        return [name, headline, description];
    }

    isEmpty() {
        return this.entities.isEmpty();
    }

    generateConfig(data) {
        const configFieldset = createFieldset('config');
        const config = new Fieldset(this.configSchema, configFieldset);
        if (config.isEmpty()) {
            return null;
        }
        if (data.config) {
            config.fill(data.config);
        }
        config.getElement().classList.add('config');
        this.container.appendChild(config.getElement());
        return config;
    }

    generateEntities(data) {
        const entitiesFieldset = createFieldset('entities');
        entitiesFieldset.classList.add('entities');
        const entitiesList = new EntitiesList(this.entitiesSchema, entitiesFieldset);
        this.container.appendChild(entitiesFieldset);

        if (data.entities) {
            const defaults = data.defaults || {};
            data.entities.forEach((entity) => {
                const entityWithDefaults = { ...defaults, ...entity };
                entitiesList.addEntry(entityWithDefaults);
            });
            this.container.open = true;
        }
        return entitiesList;
    }

    listEntities() {
        return this.entities.listEntries();
    }

    getEntity(entityName) {
        return this.entities.getEntry(entityName);
    }

    getElement() {
        return this.container;
    }

    read() {
        let config = {};
        if (this.config) {
            config = this.config.read();
        }
        const entities = this.entities.read();
        const data = { config: config, entities: entities };
        return data;
    }
    showError(path, message) {
        if (path instanceof Array) {
            if (path.length > 1) {
                if (path[0] == 'config') {
                    if (this.config) {
                        return this.config.showError(path.slice(1), message);
                    }
                } else if (path[0] == 'entities') {
                    return this.entities.showError(path.slice(1), message);
                }
            }
        }
        return null;
    }
}

class ActorsForm {
    constructor(data, actorsModel, menu) {
        this.container = document.createElement('form');
        this.menu = menu;
        this.actorSections = {};
        const subcategories = {};

        for (const [name, actorModel] of Object.entries(actorsModel)) {
            const actorData = data[name] || {};
            const actorSection = new ActorSection(
                name,
                actorData,
                actorModel.description,
                actorModel.type,
                flattenSchema(actorModel.config_schema),
                flattenSchema(actorModel.entity_schema)
            );
            if (!subcategories[actorModel.type]) {
                subcategories[actorModel.type] = {};
            }
            subcategories[actorModel.type][name] = actorSection;
            this.actorSections[name] = actorSection;
        }
        for (const [type, group] of Object.entries(subcategories)) {
            const header = this.getSubcategoryHeader(type);
            this.container.appendChild(header);

            const submenu = new MenuItem(type, this.menu);
            submenu.getElement().classList.add(getActorTypeBgClass(type));
            submenu.registerScrollHandler(header);

            for (const [name, section] of Object.entries(group)) {
                this.container.appendChild(section.getElement());

                const sectionItem = new MenuItem(name, submenu);
                sectionItem.registerScrollHandler(section.getElement());
            }
        }
    }

    getSubcategoryHeader(type) {
        const header = document.createElement('h3');
        header.innerText = type;
        header.classList.add('actor-type');
        header.classList.add(getActorTypeBgClass(type));
        return header;
    }

    listActors() {
        const names = [];
        for (const name of Object.keys(this.actorSections)) {
            names.push(name);
        }
        return names;
    }

    getActor(name) {
        return this.actorSections[name];
    }

    getElement() {
        return this.container;
    }

    read() {
        const data = {};
        for (const [name, section] of Object.entries(this.actorSections)) {
            if (!section.isEmpty()) {
                data[name] = section.read();
            }
        }
        return data;
    }
    showError(path, message) {
        if (path instanceof Array) {
            if (path.length > 2) {
                if (path[0] == 'actors') {
                    if (path[1] in this.actorSections) {
                        return this.actorSections[path[1]].showError(path.slice(2), message);
                    }
                }
            }
        }
    }
}

class ActorsInfo {
    constructor(actorsForm) {
        this.form = actorsForm;
        this._names = this.form.listActors();
        this._types = this._generateTypes();
    }
    listActors() {
        return this._names;
    }

    listEntities(actor) {
        const actorSection = this.form.getActor(actor);
        if (!actorSection) {
            return [];
        }
        return actorSection.listEntities();
    }

    listInfo(actor) {
        const actorSection = this.form.getActor(actor);
        if (!actorSection) {
            return '';
        }
        return actorSection.info;
    }

    _generateTypes() {
        const types = {};
        for (const name of this.listActors()) {
            const type = this.actorType(name);
            if (!type) {
                continue;
            }
            if (!types[type]) {
                types[type] = [];
            }
            types[type].push(name);
        }
        return types;
    }

    listTypes() {
        return this._types;
    }

    actorType(actor) {
        const actorSection = this.form.getActor(actor);
        if (!actorSection) {
            return null;
        }
        return actorSection.type;
    }

    getEntity(actorName, entityName) {
        if (this._names.includes(actorName)) {
            const actor = this.form.actorSections[actorName];
            if (this.listEntities(actorName).includes(entityName)) {
                const entity = actor.getEntity(entityName);
                if (entity) {
                    return entity;
                }
            }
        }
        return null;
    }

    scrollTo(actorName, entityName) {
        const actor = this.form.actorSections[actorName];
        const entity = this.getEntity(actorName, entityName);
        if (entity) {
            entity.getElement().scrollIntoView();
        } else if (actor) {
            actor.getElement().scrollIntoView();
        }
    }

    getEntityProperty(actorName, entityName, propertyName) {
        const entity = this.getEntity(actorName, entityName);
        if (!entity) {
            return undefined;
        }
        const data = entity.read();
        return data[propertyName];
    }

    getConsumeRecord(actorName, entityName) {
        return this.getEntityProperty(actorName, entityName, 'consume_record');
    }

    getResetOrigin(actorName, entityName) {
        return this.getEntityProperty(actorName, entityName, 'reset_origin');
    }
}
