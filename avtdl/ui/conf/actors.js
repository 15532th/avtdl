class EntitiesList {
    /**
     * @param {string} name
     * @param {any} schema
     * @param {HTMLElement | null} container
     * @param {MenuItem} parentMenu
     * @param {(oldName: string | null, newName: string | null) => void} onEntityChange
     * @param {HistoryView} historyView
     */
    constructor(name, schema, container = null, parentMenu, onEntityChange = (oldName, newName) => {}, historyView) {
        this.actorName = name;
        this.schema = schema;
        /** @type {Fieldset[]} */
        this.entries = [];
        this.container = container || document.createElement('div');
        this.container.classList.add('editable-list');
        this.menu = parentMenu;
        this.onEntityChange = onEntityChange;
        this.historyView = historyView;

        this.addButton = createButton('[Add]', () => this.addEntry(), 'add-button');
        this.addButton.title = 'Add new entity';
        this.container.appendChild(this.addButton);
    }

    isEmpty() {
        return this.entries.length == 0;
    }

    /**
     * @param {{ [x: string]: any; } | null} data
     * @returns {[Fieldset, HTMLDivElement]}
     */
    createEntry(data) {
        const entryDiv = document.createElement('div');
        entryDiv.classList.add('entry-container');

        const entity = new Fieldset(this.schema);
        if (data) {
            entity.fill(data);
            this.onEntityChange(null, entity.getName());
        }
        entryDiv.appendChild(entity.getElement());

        const buttonsContainer = createElement('div', 'entry-buttons', entryDiv);

        const deleteButton = createButton('×', () => this.deleteEntry(entity, entryDiv), 'entry-button');
        deleteButton.title = 'Delete entity';
        buttonsContainer.appendChild(deleteButton);

        const copyButton = createButton('⧉', () => this.copyEntry(entity, entryDiv), 'entry-button');
        copyButton.title = 'Duplicate entity';
        buttonsContainer.appendChild(copyButton);

        const historyButton = createButton(
            'ⓘ',
            () => {
                this.historyView.showHistory(this.actorName, entity.getName());
            },
            'entry-button'
        );
        historyButton.title = 'Show recent records';
        buttonsContainer.appendChild(historyButton);

        entity.registerNameChangeCallback((oldName, newName, nameField) => {
            this.handleNameUpdate(oldName, newName, nameField);
        });

        entity.registerNameChangeValidator((newName) => {
            if (newName === entity.getName()) {
                return null;
            }
            if (this.isNameUsed(newName)) {
                return 'name is already used';
            }
            return null;
        });
        return [entity, entryDiv];
    }

    /** @param {object?} data */
    addEntry(data = null) {
        let isNew = (data === null);
        if (isNew) {
            let name = chooseNewName('entity', this.listEntries()) || `entity ${Date.now()}`;
            data = {'name': name};
        }
        const [entity, entityDiv] = this.createEntry(data);
        this.container.insertBefore(entityDiv, this.addButton);
        this.entries.push(entity);
        if (isNew) {
            this.onEntityChange(null, entity.getName());
        }
        return entity;
    }

    /**
     * @param {Fieldset} entry
     * @param {HTMLDivElement} entryDiv
     */
    copyEntry(entry, entryDiv) {
        const data = entry.read();
        data['name'] = chooseNewName(data['name'], this.listEntries()) || data['name'];

        const [newEntity, newEntryDiv] = this.createEntry(data);
        this.container.insertBefore(newEntryDiv, entryDiv.nextSibling);
        newEntryDiv.scrollIntoView();

        const pos = this.entries.indexOf(entry) + 1;
        this.entries.splice(pos, 0, newEntity);
    }

    /**
     * @param {Fieldset} entry
     * @param {HTMLDivElement} entryDiv
     */
    deleteEntry(entry, entryDiv) {
        this.container.removeChild(entryDiv);
        this.entries = this.entries.filter((x) => x !== entry);
        this.onEntityChange(entry.getName(), null);
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

    /**
     * @param {string} name
     */
    isNameUsed(name) {
        const sameNameEntries = [];
        for (const entry of this.entries) {
            if (name == entry.getName()) {
                sameNameEntries.push(entry);
            }
        }
        return sameNameEntries.length > 0;
    }

    /**
     * @param {string | null} oldName
     * @param {string | null} newName
     * @param {NameInputField | null} nameField
     */
    handleNameUpdate(oldName, newName, nameField) {
        const sameNameEntries = [];
        for (const entry of this.entries) {
            entry.showError(['name'], ''); // clear error by setting empty error message
            if (newName == entry.getName()) {
                sameNameEntries.push(entry);
            }
        }
        if (sameNameEntries.length > 1) {
            sameNameEntries.forEach((entry) => {
                entry.showError(['name'], 'name used more than once');
            });
        }
        this.onEntityChange(oldName, newName);
    }

    /**
     * @param {string} entryName
     */
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

    /**
     * @param {string | any[]} path
     * @param {string} message
     */
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
    /**
     * @param {string} name
     * @param {any} data
     * @param {string} info
     * @param {string} type
     * @param {any} configSchema
     * @param {any} entitiesSchema
     * @param {MenuItem | null | undefined} parentMenu
     * @param {{ (actorName: string, oldName: string?, newName: string?): void}} onEntityChange
     * @param {HistoryView} historyView
     */
    constructor(name, data, info, type, configSchema, entitiesSchema, parentMenu, onEntityChange, historyView) {
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

        this.menu = new MenuItem(name, parentMenu);
        this.menu.showSubmenuCount(true);
        this.menu.registerScrollHandler(this.container);

        this.onAnyEntityChange = onEntityChange || function (actorName, oldName, newName) {};

        this.config = this.generateConfig(data);
        this.entities = this.generateEntities(data, this.menu, historyView);
    }

    onEntityChange = (/** @type {string?} */ oldName, /** @type {string?} */ newName) => {
        this.onAnyEntityChange(this.name, oldName, newName);
    };

    /**
     * @param {string} name
     * @param {string} description
     */
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

    /**
     * @param {{ entities: any[]; defaults: {}; }} data
     * @param {MenuItem} menu
     * @param {HistoryView} historyView
     */
    generateEntities(data, menu, historyView) {
        const entitiesFieldset = createFieldset('entities');
        entitiesFieldset.classList.add('entities');
        const entitiesList = new EntitiesList(
            this.name,
            this.entitiesSchema,
            entitiesFieldset,
            menu,
            this.onEntityChange,
            historyView
        );
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

    /**
     * @param {string} entityName
     */
    getEntity(entityName) {
        return this.entities.getEntry(entityName);
    }

    addEntity(data) {
        return this.entities.addEntry(data);
    }

    getElement() {
        return this.container;
    }

    read() {
        let data = {};
        if (this.config && !this.config.isEmpty()) {
            data['config'] = this.config.read();
        }
        data['entities'] = this.entities.read();
        return data;
    }
    /**
     * @param {string | any[]} path
     * @param {string} message
     */
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
    /**
     * @param {MenuItem} menu
     */
    constructor(data, actorsModel, menu) {
        this.container = document.createElement('form');
        this.menu = menu;
        this.actorSections = {};
        const subcategories = {};
        this.subcategoriesMenu = {};
        this.onEntityChangeCallbacks = [];
        this.historyView = new HistoryView(this.container);

        for (const [name, actorModel] of Object.entries(actorsModel)) {
            const actorData = data[name] || {};
            const actorType = actorModel.type;
            if (!subcategories[actorModel.type]) {
                subcategories[actorModel.type] = {};

                const header = this.getSubcategoryHeader(actorType);
                this.container.appendChild(header);

                const submenu = new MenuItem(actorType, this.menu);
                submenu.getElement().classList.add(getActorTypeBgClass(actorType));
                submenu.registerScrollHandler(header);
                this.subcategoriesMenu[actorType] = submenu;
            }

            const actorSection = new ActorSection(
                name,
                actorData,
                actorModel.description,
                actorType,
                flattenSchema(actorModel.config_schema),
                flattenSchema(actorModel.entity_schema),
                this.subcategoriesMenu[actorType],
                this.onEntityChange,
                this.historyView
            );
            subcategories[actorType][name] = actorSection;
            this.actorSections[name] = actorSection;

            this.container.appendChild(actorSection.getElement());
        }
    }

    /**
     * @param {string} type
     */
    getSubcategoryHeader(type) {
        const header = document.createElement('h3');
        header.innerText = type;
        header.classList.add('actor-type');
        header.classList.add(getActorTypeBgClass(type));
        return header;
    }

    /** @param {(actorName: string, oldName: string?, newName: string?) => void} callback  */
    registerOnEntityChangeChangeHandler(callback = (actorName, oldName, newName) => {}) {
        this.onEntityChangeCallbacks.push(callback);
    }

    /**
     * @param {string} actorName
     * @param {string?} oldName
     * @param {string?} newName
     */
    onEntityChange = (actorName, oldName, newName) => {
        for (const cb of this.onEntityChangeCallbacks) {
            cb(actorName, oldName, newName);
        }
    };

    listActors() {
        const names = [];
        for (const name of Object.keys(this.actorSections)) {
            names.push(name);
        }
        return names;
    }

    /**
     * @param {string} name
     */
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
    /**
     * @param {string | any[]} path
     * @param {string} message
     */
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
    /**
     * @param {HTMLFormElement} actorsForm
     */
    constructor(actorsForm) {
        this.form = actorsForm;
        this.historyView = new HistoryView(document.body);
        this._names = this.form.listActors();
        this._types = this._generateTypes();

        this._onEntityChangeCallbacks = [];
        this.form.registerOnEntityChangeChangeHandler(this.onEntityChange);
    }
    listActors() {
        return this._names;
    }

    /**
     * @param {string} actor
     */
    listEntities(actor) {
        const actorSection = this.form.getActor(actor);
        if (!actorSection) {
            return [];
        }
        return actorSection.listEntities();
    }

    /**
     * @param {string} actor
     */
    listInfo(actor) {
        const actorSection = this.form.getActor(actor);
        if (!actorSection) {
            return '';
        }
        return actorSection.info;
    }

    _generateTypes() {
        /** @type {{[s: string]: string[]}} */
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

    /**
     * @param {string} actor
     * @returns {string?}
     */
    actorType(actor) {
        const actorSection = this.form.getActor(actor);
        if (!actorSection) {
            return null;
        }
        return actorSection.type;
    }

    /**
     * @param {string} actorName
     * @returns {ActorSection?}
     */
    getActor(actorName) {
        if (this._names.includes(actorName)) {
            const actor = this.form.actorSections[actorName];
            return actor;
        }
        return null;
    }

    /**
     * @param {string} actorName
     * @param {string} entityName
     * @returns {Fieldset?}
     */
    getEntity(actorName, entityName) {
        const actor = this.getActor(actorName);
        if (actor != null) {
            if (this.listEntities(actorName).includes(entityName)) {
                const entity = actor.getEntity(entityName);
                if (entity) {
                    return entity;
                }
            }
        }
        return null;
    }

    /**
     * @param {string} actorName
     * @param {string} entityName
     */
    scrollTo(actorName, entityName) {
        const actor = this.form.actorSections[actorName];
        const entity = this.getEntity(actorName, entityName);
        if (entity) {
            scrollIntoView(entity.getElement());
        } else if (actor) {
            scrollIntoView(actor.getElement());
        }
    }

    /**
     * @param {string} actorName
     * @param {string} entityName
     * @param {string} propertyName
     */
    getEntityProperty(actorName, entityName, propertyName) {
        const entity = this.getEntity(actorName, entityName);
        if (!entity) {
            return undefined;
        }
        const data = entity.read();
        return data[propertyName];
    }

    /**
     * @param {string} actorName
     * @param {string} entityName
     */
    getConsumeRecord(actorName, entityName) {
        return this.getEntityProperty(actorName, entityName, 'consume_record');
    }

    /**
     * @param {string} actorName
     * @param {string} entityName
     */
    getResetOrigin(actorName, entityName) {
        return this.getEntityProperty(actorName, entityName, 'reset_origin');
    }

    /**
     * @param {(actorName: string, oldName: string?, newName: string?) => void} callback
     */
    registerOnEntityChangeChangeHandler(callback = (actorName, oldName, newName) => {}) {
        this._onEntityChangeCallbacks.push(callback);
    }

    /**
     * @type {(actorName: string, oldName: string?, newName: string?) => void}
     */
    onEntityChange = (actorName, oldName, newName) => {
        for (const cb of this._onEntityChangeCallbacks) {
            cb(actorName, oldName, newName);
        }
    };

    /**
     * @param {string} actorName
     * @param {string} entityName
     * @param {ChainCard} card
     */
    addCrossReference(actorName, entityName, card) {
        console.log('TODO add crossreference', actorName, entityName, card.read());
    }

    /**
     * @param {string} actorName
     * @param {string} entityName
     * @param {ChainCard} card
     */
    removeCrossReference(actorName, entityName, card) {
        console.log('TODO remove crossreference', actorName, entityName, card.read());
    }
}
