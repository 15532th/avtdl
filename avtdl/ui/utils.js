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

function updateTooltip(showInfo, newMessage) {
    const tooltip = showInfo.selectElement('.tooltip');
    if (!tooltip) {
        return;
    }
    tooltip.innerHTML = newMessage;
}

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

function createDefinition(text, title) {
    const dfn = document.createElement('dfn');
    dfn.innerText = text;
    dfn.title = title;
    dfn.classList.add('definition');
    return dfn;
}

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

function getUserInput(prompt, initialValue, containerElement, validator = (value) => {}) {
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
        modalContent.appendChild(closeButton);

        modalBackground.onclick = function (event) {
            if (event.target === modalBackground) {
                rejectValue();
            }
        };

        modalInput.focus();
    });
}

function openParentsDetails(node) {
    let currentNode = node;

    while (currentNode && currentNode.tagName) {
        if (currentNode.tagName.toLowerCase() === 'details') {
            currentNode.open = true;
        }
        currentNode = currentNode.parentNode;
    }
}

function getActorTypeBgClass(type) {
    return 'bg-' + type.toLowerCase();
}

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

function changeElementVisibility(element, show = true) {
    if (!show) {
        element.style.display = 'none';
    } else if (element.style.display == 'none') {
        element.style.display = 'initial';
    }
}

function getTimezonesList() {
    return document.TIMEZONES || [];
}

function registerOnClickOutside(element, callback) {
    document.addEventListener('click', (event) => {
        if (!element.contains(event.target)) {
            callback();
        }
    });
}

function observeChildMutations(element, callback) {
    if (!window.MutationObserver) {
        console.error('MutationObserver is not supported in this browser.');
        return;
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

    // Return a function to stop observing
    return () => observer.disconnect();
}

function countOccurrences(array, value) {
    return array.reduce((count, item) => (item === value ? count + 1 : count), 0);
}

function chooseNewName(base, usedNames) {
    let name = base;
    let start = 0;

    const match = name.match(/(.*) \((\d+)\)$/);
    if (match) {
        base = match[1]
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
