class InputField {
    constructor(propertyName, schema) {
        this.propertyName = propertyName; // Store the property name
        this.schema = schema;
        this.fieldContainer = generateField(this.propertyName, this.schema);
    }

    isRequired() {
        return this.schema.required;
    }

    getDefault() {
        return this.schema.default;
    }

    fill(value) {
        fillInput(this.fieldContainer, value);
    }

    read() {
        return readInput(this.fieldContainer, this.schema.default);
    }

    getElement() {
        return this.fieldContainer; // Return the container element with the input
    }

    showError(path, message) {
        if (path instanceof Array) {
            if (path.length == 0) {
                return showInputError(this.fieldContainer, message);
            }
        }
    }
}

class SuggestionsInputField {
    constructor(propertyName, schema, possibleValues) {
        this.propertyName = propertyName;
        this.schema = schema;
        this.container = generateField(this.propertyName, this.schema);
        this.container.classList.add('optsearch-container');
        this.possibleValues = possibleValues || [];

        // Create the input element
        this.inputField = this.container.querySelector('input');
        if (!this.inputField) {
            this.inputField = document.createElement('input');
            this.inputField.classList.add('optsearch-input');
            this.container.appendChild(this.inputField);
        }
        this.inputField.classList.add('optsearch-input');

        // Create the suggestions list
        this.suggestionsList = document.createElement('div');
        this.suggestionsList.className = 'optsearch-suggestions';
        this.container.appendChild(this.suggestionsList);
        this.showSuggestions(false);

        // Event listeners
        registerOnClickOutside(this.container, () => {
            this.showSuggestions(false);
        });
        this.inputField.addEventListener('focus', () => this.updateSuggestions());
        this.inputField.addEventListener('input', () => this.handleInput());

        // Debounce control variables
        this.debounceTimeout = null;
    }

    showSuggestions(show = true) {
        changeElementVisibility(this.suggestionsList, show);
    }

    updateSuggestions() {
        const inputValue = this.inputField.value.toLowerCase();
        this.suggestionsList.innerHTML = ''; // Clear previous suggestions

        // Filter possible values based on input
        const filteredValues = this.possibleValues.filter((value) => value.toLowerCase().includes(inputValue));

        if (filteredValues.length < 1) {
            this.showSuggestions(false);
            return;
        }
        if (filteredValues.length == 1 && filteredValues[0].toLowerCase() == inputValue) {
            this.showSuggestions(false);
            return;
        }

        // Display suggestions
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
        // Clear previous timeout if still running
        if (this.debounceTimeout) {
            clearTimeout(this.debounceTimeout);
        }

        // Set a new timeout for updating suggestions
        this.debounceTimeout = setTimeout(() => {
            this.updateSuggestions();
        }, 300);
    }

    selectSuggestion(value) {
        this.inputField.value = value; // Set input field value
        this.suggestionsList.innerHTML = ''; // Clear suggestions
    }

    isRequired() {
        return this.schema.required;
    }

    getDefault() {
        return this.schema.default;
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

    showError(path, message) {
        if (path instanceof Array) {
            if (path.length == 0) {
                return showInputError(this.container, message);
            }
        }
    }
}

class DictionaryInputField {
    constructor(propertyName, schema) {
        this.propertyName = propertyName; // Store the property name
        this.schema = schema;
        this.fieldContainer = createFieldset(propertyName, schema.description || null);
        this.entries = [];

        addErrorPlaceholder(this.fieldContainer);
        this.addButton = createButton('[+]', () => this.generateKeyValuePair('', '', this.schema), 'add-button');
        this.fieldContainer.appendChild(this.addButton);

        if (schema.default) {
            this.fill(schema.default);
        }
    }

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
        fieldDiv.appendChild(deleteButton);

        addErrorPlaceholder(fieldDiv, valueInput);

        this.fieldContainer.insertBefore(fieldDiv, this.addButton);
        this.entries.push(fieldDiv);
    }

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

    fill(data) {
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
            const value = readInput(valueInput, this.schema.default);
            if (key || data) {
                data[key] = value;
            }
        }
        if (!data && this.schema.default !== undefined) {
            return this.schema.default;
        }
        return data;
    }

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
    constructor(propertyName, schema) {
        this.propertyName = propertyName; // Store the property name
        this.schema = schema;
        this.fieldContainer = createFieldset(propertyName, schema.description || null);
        this.entries = [];

        addErrorPlaceholder(this.fieldContainer);
        this.addButton = createButton('[+]', () => this.generateArrayItem('', this.schema), 'add-button');
        this.fieldContainer.appendChild(this.addButton);

        if (schema.default) {
            this.fill(schema.default);
        }
    }

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
        fieldDiv.appendChild(deleteButton);

        addErrorPlaceholder(fieldDiv, valueInput);

        this.fieldContainer.insertBefore(fieldDiv, this.addButton);
        this.entries.push(fieldDiv);
    }

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
            const value = readInput(valueInput, this.schema.default);
            if (value !== null) {
                data.push(value);
            }
        }
        if (!data && this.schema.default !== undefined) {
            return this.schema.default;
        }
        return data;
    }

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
    constructor(schema, container = null) {
        this.schema = schema;
        this.fieldset = container || document.createElement('fieldset');
        this.fieldInputs = [];
        this.separatorToggler = (newState) => {};
        this.generateFieldsInputs(this.fieldset);
    }

    isEmpty() {
        return this.fieldInputs.length == 0;
    }

    generateFieldsInputs(fieldset) {
        const requiredFields = document.createElement('div');
        requiredFields.classList.add('required-fields');

        const additionalFields = document.createElement('div');
        additionalFields.classList.add('additional-fields');

        for (const [propertyName, propertySchema] of Object.entries(this.schema)) {
            let fieldInput;
            if (propertyName == 'timezone') {
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
    fill(data) {
        for (const fieldInput of this.fieldInputs) {
            const value = data[fieldInput.propertyName];
            if (value !== undefined) {
                fieldInput.fill(value);
                if (!fieldInput.isRequired() && value !== fieldInput.getDefault()) {
                    // show additional fields if at least one of them is filled
                    this.separatorToggler(true);
                }
            }
        }
    }

    // Method to read current values of input fields and return them as an object
    read() {
        const result = {};
        for (const fieldInput of this.fieldInputs) {
            result[fieldInput.propertyName] = fieldInput.read(); // Read value from each FieldInput instance
        }
        return result; // Return the object containing all field values
    }

    getElement() {
        return this.fieldset;
    }

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