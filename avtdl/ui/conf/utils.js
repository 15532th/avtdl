/**
 * @param {string} message
 */
function createTooltip(message) {
    const showInfo = document.createElement('span');
    showInfo.className = 'show-info';
    showInfo.textContent = ' 💬';

    const tooltip = document.createElement('span');
    tooltip.innerHTML = message;
    tooltip.className = 'tooltip';

    showInfo.appendChild(tooltip);
    return showInfo;
}

/**
 * @param {{ selectElement: (arg0: string) => any; }} showInfo
 * @param {string} newMessage
 */
function updateTooltip(showInfo, newMessage) {
    const tooltip = showInfo.selectElement('.tooltip');
    if (!tooltip) {
        return;
    }
    tooltip.innerHTML = newMessage;
}

/**
 * @param {string} text
 * @param {((this: GlobalEventHandlers, ev: MouseEvent) => any) | null} onClick
 * @param {string} addClass
 */
function createButton(text, onClick, addClass) {
    const button = document.createElement('button');
    button.type = 'button';
    button.innerText = text;
    button.onclick = onClick;
    if (addClass) {
        button.classList.add(addClass);
    }
    return button;
}

/**
 * @param {string} text
 * @param {string | null} tooltip
 */
function createFieldset(text, tooltip = null) {
    const fieldset = document.createElement('fieldset');
    if (text || tooltip !== null) {
        const legend = document.createElement('legend');
        const legendText = document.createElement('span');
        legendText.classList.add('legend-text');
        legendText.innerText = text;
        legend.appendChild(legendText);
        if (tooltip !== null) {
            const tooltipElement = createTooltip(tooltip);
            legend.appendChild(tooltipElement);
        }
        fieldset.appendChild(legend);
    }
    return fieldset;
}

/**
 * @param {string | null} title
 * @param {string | null} tooltip
 * @param {string | null} headline
 */
function createDetails(title, tooltip = null, headline = null) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = title;
    if (headline) {
        const headliner = document.createElement('span');
        headliner.textContent = headline;
        headliner.classList.add('summary-headline');
        summary.appendChild(headliner);
    }
    if (tooltip) {
        summary.appendChild(createTooltip(tooltip));
    }
    details.appendChild(summary);
    return details;
}

/**
 * @param {string} text
 * @param {string} title
 */
function createDefinition(text, title) {
    const dfn = document.createElement('dfn');
    dfn.innerText = text;
    dfn.title = title;
    dfn.classList.add('definition');
    return dfn;
}

/**
 * @param {string} src
 * @param {string?} className
 * @param {HTMLElement?} parentElement
 */
function createImage(src, className, parentElement=null) {
    const img = document.createElement('img');
    img.src = src;
    img.alt = src;
    if (className) {
        img.classList.add(className);
    }
    if (parentElement) {
        parentElement.appendChild(img);
    }
    return img;
}

/**
 * @param {string} tag
 * @param {string | undefined} [className]
 * @param {HTMLElement | undefined} [parentElement]
 */
function createElement(tag, className, parentElement) {
    const element = document.createElement(tag);
    if (className) {
        element.classList.add(className);
    }
    if (parentElement) {
        parentElement.appendChild(element);
    }
    return element;
}

/**
 * @param {HTMLElement} container
 * @param {HTMLInputElement | HTMLSelectElement | undefined} [associatedInput]
 */
function addErrorPlaceholder(container, associatedInput) {
    const errorMessage = document.createElement('div');
    errorMessage.classList.add('form-error');
    container.appendChild(errorMessage);
    if (associatedInput) {
        associatedInput.addEventListener('input', () => {
            clearInputError(container);
        });
        associatedInput.addEventListener('focus', () => {
            clearInputError(container);
        });
    }
    return errorMessage;
}

/**
 * @param {string | null} prompt
 * @param {string | undefined} [initialValue]
 * @param {HTMLElement | undefined} [containerElement]
 * @param {(value: string) => string | null} validator
 */
function getUserInput(
    prompt,
    initialValue,
    containerElement,
    validator = (value) => {
        return null;
    }
) {
    return new Promise((resolve, reject) => {
        const container = containerElement || document.body;
        const modalBackground = createElement('div', 'modal-background', container);
        const modalContent = createElement('div', 'modal-content', modalBackground);

        const promptText = document.createElement('div');
        promptText.textContent = prompt;
        modalContent.appendChild(promptText);

        const modalInput = document.createElement('input');
        modalInput.type = 'text';
        modalInput.value = initialValue || '';
        modalInput.className = 'modal-input';
        modalContent.appendChild(modalInput);

        const modalError = addErrorPlaceholder(modalContent, modalInput);

        const acceptValue = function () {
            if (modalInput.value) {
                const error = validator(modalInput.value);
                if (!error) {
                    resolve(modalInput.value);
                } else {
                    modalError.innerText = error;
                    modalError.style.display = 'block';
                    return;
                }
            } else {
                reject(modalInput.value);
            }
            container.removeChild(modalBackground);
        };

        const okButton = createButton('OK', acceptValue, 'modal-button');
        modalContent.appendChild(okButton);

        const rejectValue = function () {
            container.removeChild(modalBackground);
            reject(null);
        };

        const closeButton = createButton('×', rejectValue, 'close-button');
        closeButton.title = 'Cancel';
        modalContent.appendChild(closeButton);

        modalInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === 'NumpadEnter') {
                acceptValue();
            } else if (event.key === 'Escape') {
                rejectValue();
            }
        });

        modalBackground.onclick = function (event) {
            if (event.target === modalBackground) {
                rejectValue();
            }
        };

        modalInput.focus();
    });
}

/**
 * @param {Node} node
 */
function openParentsDetails(node) {
    /** @type {Node | null} */
    let currentNode = node;

    while (currentNode !== null) {
        if (currentNode instanceof HTMLDetailsElement) {
            currentNode.open = true;
        }
        currentNode = currentNode.parentNode;
    }
}

/**
 * @param {string} type
 */
function getActorTypeBgClass(type) {
    return 'bg-' + type.toLowerCase();
}

/**
 * @param {HTMLElement} targetElement
 */
function scrollIntoView(targetElement) {
    openParentsDetails(targetElement);
    targetElement.scrollIntoView(true);

    if (!targetElement.classList.contains('highlight')) {
        targetElement.classList.add('bg-highlight');
        targetElement.classList.add('highlight');
        setTimeout(() => {
            targetElement.classList.remove('bg-highlight');
        }, 1000);
        setTimeout(() => {
            targetElement.classList.remove('highlight');
        }, 3000);
    }
}

/**
 * @param {HTMLElement} element
 */
function changeElementVisibility(element, show = true) {
    if (!show) {
        element.style.display = 'none';
    } else if (element.style.display == 'none') {
        element.style.display = 'initial';
    }
}

function getTimezonesList() {
    return document['TIMEZONES'] || [];
}

/**
 * @param {HTMLDivElement} element
 * @param {{ (): void; (): void; }} callback
 */
function registerOnClickOutside(element, callback) {
    document.addEventListener('click', (event) => {
        // @ts-ignore
        if (!element.contains(event.target)) {
            callback();
        }
    });
}

/**
 * @param {Node} element
 * @param {{ (): void; (): void; (arg0: MutationRecord): void; }} callback
 * @returns {{(): void}}
 */
function observeChildMutations(element, callback) {
    if (!window.MutationObserver) {
        console.error('MutationObserver is not supported in this browser.');
        return () => {};
    }

    const observer = new MutationObserver((mutationsList) => {
        for (const mutation of mutationsList) {
            if (mutation.type === 'childList') {
                callback(mutation);
            }
        }
    });
    const config = { childList: true, subtree: true };
    observer.observe(element, config);

    // return a function to stop observing
    return () => observer.disconnect();
}

/**
 * @param {any[]} array
 * @param {any} value
 * @return {number}
 */
function countOccurrences(array, value) {
    return array.reduce((count, item) => (item === value ? count + 1 : count), 0);
}

/**
 * @param {string} base
 * @param {string[] | OrderedDict} usedNames
 */
function chooseNewName(base, usedNames) {
    let name = base;
    let start = 0;

    const match = name.match(/(.*) \((\d+)\)$/);
    if (match) {
        base = match[1];
        start = Number(match[2]) + 1;
    }
    for (let i = start; i < 1000; i++) {
        name = `${base} (${i})`;
        if (usedNames instanceof Array) {
            if (!usedNames.includes(name)) {
                return name;
            }
        } else if (usedNames instanceof Object) {
            if (!(name in usedNames)) {
                return name;
            }
        }
    }
    return null;
}

class OrderedDict {
    constructor() {
        this.data = {};
        this.order = [];
        this.proxy = this.createProxy();
        return this.proxy;
    }

    createProxy() {
        return new Proxy(this, {
            has: (target, prop) => {
                return prop in target.data;
            },
            deleteProperty: (target, prop) => {
                if (prop in target.data) {
                    delete target.data[prop];
                    target.order = target.order.filter((key) => key !== prop);
                    return true;
                }
                return false;
            },
            ownKeys: (target) => {
                // Return keys in insertion order
                return [...target.order];
            },
            getOwnPropertyDescriptor: (target, prop) => {
                if (prop in target.data) {
                    const descriptor = {
                        configurable: true,
                        enumerable: true,
                        value: target.data[prop],
                    };
                    console.log(descriptor);
                    return descriptor;
                }
                return { configurable: true, enumerable: false };
            },
        });
    }

    /**
     * @param {PropertyKey} key
     * @param {any} value
     */
    set(key, value) {
        if (!this.data.hasOwnProperty(key)) {
            this.order.push(key);
        }
        this.data[key] = value;
    }

    /**
     * @param {PropertyKey} key
     */
    get(key) {
        return this.data[key];
    }

    *[Symbol.iterator]() {
        for (const key of this.order) {
            yield [key, this.data[key]];
        }
    }

    /**
     * @param {PropertyKey} existingKey
     * @param {PropertyKey} newKey
     * @param {any} newValue
     */
    insertAfter(existingKey, newKey, newValue) {
        if (newKey in this.data) {
            this.order.splice(this.order.indexOf(newKey), 1);
            delete this.data[newKey];
        }
        const index = this.order.indexOf(existingKey);
        if (index !== -1) {
            this.data[newKey] = newValue;
            this.order.splice(index + 1, 0, newKey);
        } else {
            this.set(newKey, newValue);
        }
    }

    /**
     * @param {any} existingValue
     * @param {PropertyKey} newKey
     * @param {any} newValue
     */
    insertAfterValue(existingValue, newKey, newValue) {
        const existingKey = this.order.find((key) => this.data[key] === existingValue);
        return this.insertAfter(existingKey, newKey, newValue);
    }

    /**
     * @param {PropertyKey} name
     */
    move(name, steps = 1) {
        const index = this.order.indexOf(name);
        if (index > -1) {
            const newIndex = index + steps;

            if (newIndex < 0) {
                this.order.splice(index, 1);
                this.order.unshift(name);
            } else if (newIndex >= this.order.length) {
                this.order.splice(index, 1);
                this.order.push(name);
            } else if (newIndex !== index) {
                this.order.splice(index, 1);
                this.order.splice(newIndex, 0, name);
            }
        }
    }
}

/**
 * @param {HTMLElement} element
 */
function moveElement(element, forward = true) {
    if (!element.parentElement) {
        return;
    }
    const parent = element.parentElement;
    if (forward && element === parent.lastChild) {
        return;
    }
    if (!forward && element === parent.firstChild) {
        return;
    }
    const sibling = forward ? element.nextSibling && element.nextSibling.nextSibling : element.previousSibling;
    parent.removeChild(element);
    parent.insertBefore(element, sibling);
}

/**
 * Get Retry-After value from response headers
 *
 * @param {Response} response
 */
function getRetryAfter(response) {
    const retryAfter = response.headers.get('Retry-After');
    if (retryAfter) {
        try {
            const parsedValue = parseInt(retryAfter, 10);
            return !isNaN(parsedValue) ? parsedValue : null;
        } catch (error) {
            return null;
        }
    }
    return null;
}

/**
 * Checks if the provided object or array is empty.
 *
 * @param {Object|any[]} obj
 * @returns {boolean}
 */
function isEmpty(obj) {
    if (obj instanceof Array) {
        return obj.length === 0;
    }
    return Object.keys(obj).length === 0;
}

class MessageArea {
    /**
     * @param {HTMLElement} container
     */
    constructor(container) {
        this.container = container;
    }

    /**
     * @param {string} message
     * @param {string} type
     * @param {{(): void} | null} onClick
     */
    showMessage(message, type = 'success', onClick = null) {
        const messageContainer = document.createElement('div');
        messageContainer.classList.add('message-container');
        messageContainer.classList.add(type);
        this.container.appendChild(messageContainer);

        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message');
        messageContainer.appendChild(messageDiv);

        for (const line of message.split('\n')) {
            const p = document.createElement('p');
            p.innerText = line;
            messageDiv.appendChild(p);
        }

        if (onClick instanceof Function) {
            messageDiv.style.cursor = 'pointer';
            messageDiv.addEventListener('click', () => {
                onClick();
            });
        }

        const closeButton = document.createElement('button');
        closeButton.classList.add('close-button');
        closeButton.innerHTML = '&times;';
        messageContainer.appendChild(closeButton);
        closeButton.addEventListener('click', () => {
            this.removeAfter(messageContainer, 0);
        });
        if (type === 'success') {
            this.removeAfter(messageContainer, 5000);
        }
        if (type === 'info') {
            this.removeAfter(messageContainer, 15000);
        }
        return messageContainer;
    }

    /**
     * @param {string} message
     * @param {{(): void} | null} onClick
     */
    showError(message, onClick = () => {}) {
        return this.showMessage(message, 'error', onClick);
    }

    /**
     * @param {HTMLDivElement} node
     */
    removeAfter(node, delay = 5000) {
        const remove = () => {
            try {
                this.container.removeChild(node);
            } catch (error) {
                if (!(error instanceof DOMException)) {
                    throw error;
                }
            }
        };
        if (delay > 0) {
            setTimeout(remove, delay);
        } else {
            remove();
        }
    }

    clear() {
        this.container.innerHTML = '';
    }
}

/**
 * @param {string} path
 * @param {MessageArea | undefined} [messageArea]
 */
async function fetchJSON(path, messageArea, retries = 0) {
    let retryDelay = 3;
    try {
        const response = await fetch(path);
        if (!response.ok) {
            retryDelay = getRetryAfter(response) || 3;
            throw new Error(`got ${response.status} (${response.statusText}) when requesting ${path}`);
        }
        const data = await response.json();
        return data;
    } catch (error) {
        console.error(`[fetchJson]: error fetching "${path}": ${error}`);
        if (retries > 0) {
            console.log(`[fetchJson]: retrying after ${retryDelay}`);
            await new Promise((resolve) => setTimeout(resolve, retryDelay * 1000));
            return await fetchJSON(path, messageArea, retries - 1);
        }
        if (messageArea) {
            messageArea.showMessage('Error fetching data. Check if avtdl is running on correct port.', 'error');
        }
        return null;
    }
}

/**
 * @param {HTMLElement} parent
 */
function renderModal(parent, onClose = () => {}) {
    const background = createElement('div', 'modal-background', parent);
    const container = createElement('div', 'modal-view', background);
    background.onclick = (event) => {
        if (event.target === background) {
            onClose();
            parent.removeChild(background);
        }
    };
    background.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            onClose();
            parent.removeChild(background);
        }
    });
    return container;
}
