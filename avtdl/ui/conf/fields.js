class InputField {
    /**
     * @param {string} propertyName
     * @param {any} schema
     */
    constructor(propertyName, schema) {
        this.propertyName = propertyName;
        this.schema = schema;
        this.fieldContainer = generateField(this.propertyName, this.schema);
    }

    isRequired() {
        return this.schema.required;
    }

    getDefault() {
        return this.schema.default;
    }

    isDefault() {
        return this.read() == this.getDefault();
    }

    fill(value) {
        fillInput(this.fieldContainer, value);
    }

    read() {
        return readInput(this.fieldContainer, this.schema);
    }

    getElement() {
        return this.fieldContainer;
    }

    /**
     * @param {string | string[]} path
     * @param {string} message
     */
    showError(path, message) {
        if (path instanceof Array) {
            if (path.length == 0) {
                return showInputError(this.fieldContainer, message);
            }
        }
    }
}

class NameInputField extends InputField {
    /**
     * @param {string} propertyName
     * @param {any} schema
     */
    constructor(propertyName, schema) {
        super(propertyName, schema);
        this.input = selectInput(this.fieldContainer);
        if (!this.input) {
            throw(`error creating name input field ${propertyName}: no input in container`)
        }
        this.input.classList.add('name-input');
        /** @type {(newName: string) => null | string} */
        this.nameValidator = (newName) => {
            return null;
        };
        this.addRenameHandler(this.input);
    }

    /**
     * @param {(newName: string) => null | string} callback
     */
    registerNameValidator(callback) {
        this.nameValidator = callback;
    }

    /**
     * @param {HTMLInputElement} input
     */
    addRenameHandler(input) {
        input.addEventListener('focus', () => {
            if (!input.value) {
                return;
            }
            const oldName = input.value || '';
            getUserInput('New name for entity ' + oldName, oldName, this.fieldContainer, this.nameValidator).then(
                (newName) => {
                    input.value = newName;
                    input.dispatchEvent(new Event('input'));
                }
            );
        });
    }
}

class SuggestionsInputField {
    /**
     * @param {string} propertyName
     * @param {any} schema
     * @param {string[]} possibleValues
     */
    constructor(propertyName, schema, possibleValues) {
        this.propertyName = propertyName;
        this.schema = schema;
        this.container = generateField(this.propertyName, this.schema);
        this.container.classList.add('optsearch-container');
        this.possibleValues = possibleValues || [];

        this.inputField = selectInput(this.container);
        if (!this.inputField) {
            this.inputField = document.createElement('input');
            this.inputField.classList.add('optsearch-input');
            this.container.appendChild(this.inputField);
        }
        this.inputField.classList.add('optsearch-input');

        this.suggestionsList = document.createElement('div');
        this.suggestionsList.className = 'optsearch-suggestions';
        this.container.appendChild(this.suggestionsList);
        this.showSuggestions(false);

        registerOnClickOutside(this.container, () => {
            this.showSuggestions(false);
        });
        this.inputField.addEventListener('focus', () => this.updateSuggestions());
        this.inputField.addEventListener('input', () => this.handleInput());

        this.debounceTimeout = null;
    }

    showSuggestions(show = true) {
        changeElementVisibility(this.suggestionsList, show);
    }

    updateSuggestions() {
        const inputValue = this.inputField.value.toLowerCase();
        this.suggestionsList.innerHTML = '';

        const filteredValues = this.possibleValues.filter((value) => value.toLowerCase().includes(inputValue));

        if (filteredValues.length < 1) {
            this.showSuggestions(false);
            return;
        }
        if (filteredValues.length == 1 && filteredValues[0].toLowerCase() == inputValue) {
            this.showSuggestions(false);
            return;
        }

        filteredValues.forEach((value) => {
            const suggestionItem = document.createElement('div');
            suggestionItem.className = 'optsearch-suggestion';
            suggestionItem.textContent = value;
            suggestionItem.addEventListener('click', () => this.selectSuggestion(value));
            this.suggestionsList.appendChild(suggestionItem);
        });

        this.showSuggestions(true);
    }

    handleInput() {
        if (this.debounceTimeout) {
            clearTimeout(this.debounceTimeout);
        }
        this.debounceTimeout = setTimeout(() => {
            this.updateSuggestions();
        }, 300);
    }

    /**
     * @param {string} value
     */
    selectSuggestion(value) {
        this.inputField.value = value;
        this.suggestionsList.innerHTML = '';
    }

    isRequired() {
        return this.schema.required;
    }

    getDefault() {
        return this.schema.default;
    }

    isDefault() {
        return this.read() == this.getDefault();
    }

    fill(value) {
        this.inputField.value = value;
    }

    read() {
        return this.inputField.value || null;
    }

    getElement() {
        return this.container;
    }

    /**
     * @param {string | string[]} path
     * @param {string} message
     */
    showError(path, message) {
        if (path instanceof Array) {
            if (path.length == 0) {
                return showInputError(this.container, message);
            }
        }
    }
}

class DictionaryInputField {
    /**
     * @param {string} propertyName
     * @param {{ description: string; default: any; required: boolean}} schema
     */
    constructor(propertyName, schema) {
        this.propertyName = propertyName;
        this.schema = schema;
        this.fieldContainer = createFieldset(propertyName, schema.description || null);
        this.entries = [];

        addErrorPlaceholder(this.fieldContainer);
        this.addButton = createButton('[+]', () => this.generateKeyValuePair('', '', this.schema), 'add-button');
        this.addButton.title = 'Add new empty pair';
        this.fieldContainer.appendChild(this.addButton);

        if (schema.default) {
            this.fill(schema.default);
        }
    }

    /**
     * @param {string} key
     * @param {string} value
     * @param {{ description?: string; default?: any; additionalProperties?: any; }} schema
     */
    generateKeyValuePair(key, value, schema) {
        const keySchema = { type: 'string', default: key };
        let valueSchema;

        if (schema.additionalProperties) {
            const additionalProperties = schema.additionalProperties;
            valueSchema = { ...additionalProperties };
        } else {
            valueSchema = { type: 'string', default: JSON.stringify(value) };
        }
        if (value) {
            valueSchema.default = value;
        }

        const keyInput = generateInput(keySchema);
        keyInput.classList.add('key-field');
        const valueInput = generateInput(valueSchema);
        valueInput.classList.add('value-field');
        const separator = document.createElement('span');
        separator.textContent = ':';

        const fieldDiv = document.createElement('div');
        fieldDiv.classList.add('field-container');

        fieldDiv.appendChild(keyInput);
        fieldDiv.appendChild(separator);
        fieldDiv.appendChild(valueInput);

        const deleteButton = createButton('[×]', () => this.deleteEntry(fieldDiv), 'delete-field');
        deleteButton.title = 'Delete';
        fieldDiv.appendChild(deleteButton);

        addErrorPlaceholder(fieldDiv, valueInput);

        this.fieldContainer.insertBefore(fieldDiv, this.addButton);
        this.entries.push(fieldDiv);
        if (!key) {
            keyInput.focus();
        }
    }

    /**
     * @param {HTMLDivElement} entryDiv
     */
    deleteEntry(entryDiv) {
        this.fieldContainer.removeChild(entryDiv);
        this.entries = this.entries.filter((x) => x !== entryDiv);
    }

    isRequired() {
        return this.schema.required;
    }

    getDefault() {
        return this.schema.default;
    }

    isDefault() {
        const currentValue = JSON.stringify(this.read());
        const defaultValue = JSON.stringify(this.getDefault());
        return currentValue == defaultValue;
    }

    fill(data) {
        if (!data) {
            return;
        }
        this.entries.forEach((entry) => this.deleteEntry(entry));

        for (const [key, value] of Object.entries(data)) {
            this.generateKeyValuePair(key, value, this.schema);
        }
    }

    read() {
        const data = {};
        for (const fieldDiv of this.entries) {
            const keyInput = fieldDiv.querySelector('.key-field');
            const valueInput = fieldDiv.querySelector('.value-field');

            const key = readInput(keyInput);
            const value = readInput(valueInput, this.schema);
            if (key || data) {
                data[key] = value;
            }
        }
        if (isEmpty(data)) {
            if (this.schema.default !== undefined) {
                return this.schema.default;
            } else {
                return null;
            }
        }
        return data;
    }

    /**
     * @param {string | string[]} path
     * @param {string} message
     */
    showError(path, message) {
        if (path instanceof Array) {
            if (path.length == 0) {
                return showInputError(this.fieldContainer, message);
            } else if (path.length >= 1) {
                for (const fieldDiv of this.entries) {
                    const keyInput = fieldDiv.querySelector('.key-field');
                    let key = readInput(keyInput);
                    if (key == path[0]) {
                        return showInputError(fieldDiv, message);
                    }
                }
                return showInputError(this.fieldContainer, message);
            }
        }
    }

    getElement() {
        return this.fieldContainer;
    }
}

class ArrayInputField {
    /**
     * @param {string} propertyName
     * @param {{ description: string; default: any; required: boolean}} schema
     */
    constructor(propertyName, schema) {
        this.propertyName = propertyName;
        this.schema = schema;
        this.fieldContainer = createFieldset(propertyName, schema.description || null);
        this.entries = [];

        addErrorPlaceholder(this.fieldContainer);
        this.addButton = createButton('[+]', () => this.generateArrayItem('', this.schema), 'add-button');
        this.addButton.title = 'Add new empty item';
        this.fieldContainer.appendChild(this.addButton);

        if (schema.default) {
            this.fill(schema.default);
        }
    }

    /**
     * @param {string} value
     * @param {{ description?: string; default?: any; required?: boolean; items?: any; additionalProperties?: any; }} schema
     */
    generateArrayItem(value, schema) {
        let valueSchema;

        if (schema.items) {
            const additionalProperties = schema.additionalProperties;
            valueSchema = { ...additionalProperties };
        } else {
            valueSchema = { type: 'string', default: JSON.stringify(value) };
        }
        if (value) {
            valueSchema.default = value;
        }

        const valueInput = generateInput(valueSchema);
        valueInput.classList.add('value-field');

        const fieldDiv = document.createElement('div');
        fieldDiv.classList.add('field-container');

        fieldDiv.appendChild(valueInput);

        const deleteButton = createButton('[×]', () => this.deleteEntry(fieldDiv), 'delete-field');
        deleteButton.title = 'Delete';
        fieldDiv.appendChild(deleteButton);

        addErrorPlaceholder(fieldDiv, valueInput);

        this.fieldContainer.insertBefore(fieldDiv, this.addButton);
        this.entries.push(fieldDiv);
        if (!value) {
            valueInput.focus();
        }
    }

    /**
     * @param {HTMLDivElement} entryDiv
     */
    deleteEntry(entryDiv) {
        this.fieldContainer.removeChild(entryDiv);
        this.entries = this.entries.filter((x) => x !== entryDiv);
    }

    isRequired() {
        return this.schema.required;
    }

    getDefault() {
        return this.schema.default;
    }

    isDefault() {
        const currentValue = JSON.stringify(this.read());
        const defaultValue = JSON.stringify(this.getDefault());
        return currentValue == defaultValue;
    }

    /**
     * @param {any[]} data
     */
    fill(data) {
        if (!(data instanceof Array)) {
            return;
        }
        this.entries.forEach((entry) => this.deleteEntry(entry));

        data.forEach((value) => {
            this.generateArrayItem(value, this.schema);
        });
    }

    read() {
        const data = [];
        for (const fieldDiv of this.entries) {
            const valueInput = fieldDiv.querySelector('.value-field');
            const value = readInput(valueInput, this.schema);
            if (value !== null) {
                data.push(value);
            }
        }
        if (data.length == 0) {
            if (this.schema.default !== undefined) {
                return this.schema.default;
            } else {
                return null;
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
            if (path.length == 0) {
                return showInputError(this.fieldContainer, message);
            } else if (path.length == 1) {
                const index = path[0];
                if (Number.isInteger(index)) {
                    if (this.entries.length >= index) {
                        return showInputError(this.entries[index], message);
                    }
                }
                return showInputError(this.fieldContainer, message);
            }
        }
    }

    getElement() {
        return this.fieldContainer;
    }
}

class Fieldset {
    /**
     * @param {any} schema
     * @param {HTMLElement?} container
     */
    constructor(schema, container = null) {
        this.schema = schema;
        this.fieldset = container || document.createElement('fieldset');
        this.fieldInputs = [];

        this.nameField = null;
        this.nameInput = null;
        this.oldName = null;
        this.separatorToggler = (newState) => {};
        this.generateFieldsInputs(this.fieldset);
    }

    isEmpty() {
        return this.fieldInputs.length == 0;
    }

    /**
     * @param {HTMLElement} fieldset
     */
    generateFieldsInputs(fieldset) {
        const requiredFields = document.createElement('div');
        requiredFields.classList.add('required-fields');

        const additionalFields = document.createElement('div');
        additionalFields.classList.add('additional-fields');

        for (const [propertyName, propertySchema] of Object.entries(this.schema)) {
            let fieldInput;
            if (propertyName == 'name') {
                fieldInput = new NameInputField(propertyName, propertySchema);
                this.nameField = fieldInput;
                this.nameInput = selectInput(fieldInput.getElement());
            } else if (propertyName == 'timezone') {
                const timezonesList = getTimezonesList();
                fieldInput = new SuggestionsInputField(propertyName, propertySchema, timezonesList);
            } else {
                switch (propertySchema.type) {
                    case 'object':
                        fieldInput = new DictionaryInputField(propertyName, propertySchema);
                        break;
                    case 'array':
                        fieldInput = new ArrayInputField(propertyName, propertySchema);
                        break;
                    default:
                        fieldInput = new InputField(propertyName, propertySchema);
                }
            }
            this.fieldInputs.push(fieldInput);
            if (fieldInput.isRequired()) {
                requiredFields.appendChild(fieldInput.getElement());
            } else {
                additionalFields.appendChild(fieldInput.getElement());
            }
        }
        if (requiredFields.childElementCount) {
            fieldset.appendChild(requiredFields);
        }
        if (additionalFields.childElementCount) {
            fieldset.appendChild(additionalFields);
        }
        if (requiredFields.childElementCount && additionalFields.childElementCount) {
            this.addSeparator(additionalFields);
        }
    }

    /**
     * @param {HTMLElement | null} additional
     */
    addSeparator(additional) {
        const separator = document.createElement('div');
        separator.classList.add('toggle-additional');
        separator.title = 'Show/hide optional fields';
        this.fieldset.insertBefore(separator, additional);
        const separatorToggler = this.makeSeparatorToggler(separator, additional);
        separator.addEventListener('click', () => separatorToggler());
        this.separatorToggler = separatorToggler;
        this.separatorToggler(false);
    }

    /**
     * @param {HTMLDivElement} separator
     * @param {HTMLElement | null} additionalFields
     */
    makeSeparatorToggler(separator, additionalFields) {
        {
            return (newState) => {
                if (!separator || !additionalFields) {
                    return;
                }
                const targetState = newState || additionalFields.classList.contains('hidden');
                if (targetState) {
                    separator.innerText = '[-]';
                    additionalFields.classList.remove('hidden');
                } else {
                    separator.innerText = `[+] ${additionalFields.childElementCount} more options`;
                    additionalFields.classList.add('hidden');
                }
            };
        }
    }

    getName() {
        if (!this.nameInput) {
            return '';
        }
        return this.nameInput.value;
    }

    /**
     * @param {{ (oldName: string, newName: string, nameField: NameInputField | null): void}} callback
     */
    registerNameChangeCallback(callback) {
        if (this.nameInput) {
            this.nameInput.addEventListener('input', (event) => {
                if (!event.target) {
                    return;
                }
                // @ts-ignore
                const value = event.target.value;
                if (!value) {
                    return;
                }
                callback(this.oldName, value, this.nameField);

                this.oldName = value;
            });
        }
    }

    /**
     * @param {{(newName: string): null | string}} callback
     */
    registerNameChangeValidator(callback) {
        if (this.nameField) {
            this.nameField.registerNameValidator(callback);
        }
    }

    /**
     * @param {{ [x: string]: any; }} data
     */
    fill(data) {
        for (const fieldInput of this.fieldInputs) {
            const value = data[fieldInput.propertyName];
            if (value !== undefined) {
                fieldInput.fill(value);
                if (!fieldInput.isRequired()) {
                    // workaround for update_interval scheme missing "default"
                    if (fieldInput.getDefault() === undefined) {
                        continue;
                    }
                    if (JSON.stringify(value) === JSON.stringify(fieldInput.getDefault())) {
                        continue;
                    }
                    // show additional fields if at least one of them is filled
                    this.separatorToggler(true);
                }
            }
        }
        this.oldName = this.getName();
    }

    read() {
        const result = {};
        for (const fieldInput of this.fieldInputs) {
            const value = fieldInput.read();
            if (!fieldInput.isDefault()) {
                result[fieldInput.propertyName] = value;
            }
        }
        return result;
    }

    getElement() {
        return this.fieldset;
    }

    /**
     * @param {string | any[]} path
     * @param {string} message
     */
    showError(path, message) {
        if (path instanceof Array) {
            if (path.length > 0) {
                for (const field of this.fieldInputs) {
                    if (field.propertyName == path[0]) {
                        if (!field.isRequired()) {
                            this.separatorToggler(true);
                        }
                        return field.showError(path.slice(1), message);
                    }
                }
            }
        }
    }
}
