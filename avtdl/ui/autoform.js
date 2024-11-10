function generateField(propertyName, schema) {
    // Create a div to hold the field
    const fieldDiv = document.createElement('div');
    fieldDiv.className = 'form-field';

    // Create a label for the input field
    const label = document.createElement('label');
    label.className = 'form-label';
    label.textContent = propertyName; // Use title from schema or the property name
    label.setAttribute('for', propertyName);

    if (schema.description) {
        const helpTooltip = createTooltip(schema.description || '');
        label.appendChild(helpTooltip);
    }
    fieldDiv.appendChild(label);
    const inputField = generateInput(schema);
    fieldDiv.appendChild(inputField);

    addErrorPlaceholder(fieldDiv, inputField);
    return fieldDiv;
}

function generateInput(schema) {
    let inputField;

    switch (schema.type) {
        case 'string':
            // Handle string inputs
            if (schema.enum) {
                // If there's an enum, create a dropdown
                inputField = document.createElement('select');
                for (const [index, option] of Object.entries(schema.enum)) {
                    const opt = document.createElement('option');
                    opt.value = option;
                    opt.textContent = option;
                    if (option === schema.default) {
                        opt.selected = true; // Set this option as selected
                    }
                    inputField.appendChild(opt);
                }
            } else {
                inputField = document.createElement('input');
                inputField.type = 'text'; // Text input for strings
                if (schema.default) {
                    inputField.value = schema.default;
                }
            }
            break;

        case 'integer':
            // Handle integer inputs
            inputField = document.createElement('input');
            inputField.type = 'number'; // Number input for integers
            if (schema.default) {
                inputField.value = schema.default;
            }
            break;

        case 'boolean':
            // Handle boolean inputs
            inputField = document.createElement('input');
            inputField.type = 'checkbox'; // Checkbox for booleans
            if (schema.default) {
                inputField.checked = Boolean(schema.default);
            }
            break;

        default:
            // Handle unknown types by using a basic text input
            inputField = document.createElement('input');
            inputField.type = 'text';
            if (schema.default) {
                inputField.value = schema.default;
            }
            break;
    }

    inputField.required = schema.required;

    inputField.className = 'form-input';
    return inputField;
}

function selectInput(fieldContainer) {
    return fieldContainer.querySelector('.form-input');
}

function fillInput(fieldContainer, value) {
    const inputField = selectInput(fieldContainer);
    if (!inputField) {
        return;
    }
    switch (inputField.type) {
        case 'checkbox':
            inputField.checked = Boolean(value);
            break;
        case 'number':
            inputField.value = Number(value);
            break;
        case 'string':
        case 'select-one':
        default:
            inputField.value = value;
            break;
    }
}

function readInput(fieldContainer, defaultValue) {
    let value;
    const inputField = selectInput(fieldContainer) || fieldContainer;
    switch (inputField.type) {
        case 'checkbox':
            value = inputField.checked; // Return boolean value
            break;
        case 'number':
            value = Number(inputField.value); // Convert to number
            break;
        case 'string':
        case 'select-one':
        default:
            if (inputField.value === '') {
                value = null;
            } else {
                value = inputField.value; // Return string value
            }
    }
    if (value === null && defaultValue !== undefined) {
        return defaultValue;
    } else {
        return value;
    }
}

function showInputError(fieldContainer, message) {
    const errorMessage = fieldContainer.querySelector('.form-error');
    errorMessage.innerText = message;
    errorMessage.style.display = 'flex';
    return fieldContainer;
}

function clearInputError(fieldContainer) {
    const errorMessage = fieldContainer.querySelector('.form-error');
    if (!errorMessage) {
        return;
    }
    errorMessage.innerText = '';
    errorMessage.style.display = 'none';
}
