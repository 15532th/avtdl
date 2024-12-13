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

async function initializeTimezoneList() {
    if (document['TIMEZONES']) {
        return;
    }
    const timezones = await fetchJSON('/timezones');
    if (timezones instanceof Array) {
        document['TIMEZONES'] = timezones;
    }
}

function registerFormContentMonitor(rawSection, form) {
    let updateInput = () => {
        const content = form.read();
        rawSection.fill(content);
    };
    form.getElement().addEventListener('input', updateInput);
    form.getElement().addEventListener('change', updateInput);
    form.getElement().addEventListener('click', updateInput);
}

class MessageArea {
    constructor(container) {
        this.container = container;
    }

    showMessage(message, type = 'success', onCLick = null) {
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

        if (onCLick instanceof Function) {
            messageDiv.style.cursor = 'pointer';
            messageDiv.addEventListener('click', () => {
                onCLick();
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

    showError(message, onClick = () => {}) {
        return this.showMessage(message, 'error', onClick);
    }

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

class RawSection {
    constructor(sectionData) {
        this.data = sectionData;
        this.input = document.createElement('textarea');
        this.input.rows = 6;
        if (sectionData) {
            this.fill(sectionData);
        }
    }

    getElement() {
        return this.input;
    }

    fill(data) {
        const content = JSON.stringify(data, null, 2);
        this.input.value = content;
    }

    read() {
        const data = JSON.parse(this.input.value);
        return data;
    }
}

class ConfigEditor {
    constructor(container, messageArea, navigationArea) {
        this.container = container;
        this.navigationArea = navigationArea;
        this.messageArea = messageArea || new MessageArea();
        this.sections = {};
    }

    async fetchJSON(path) {
        return await fetchJSON(path, this.messageArea);
    }

    makeTopLevelSectionContainer(title) {
        const details = document.createElement('details');
        details.open = true;
        details.classList.add('top-level-section');

        const summary = document.createElement('summary');
        summary.textContent = title;

        details.appendChild(summary);
        return details;
    }

    clear() {
        this.container.innerHTML = '';
        this.navigationArea.innerHTML = '';
        this.sections = {};
    }

    async render() {
        await initializeTimezoneList();
        const data = await this.fetchJSON('/config');
        if (data === null) {
            return;
        }
        this.clear();

        for (const [name, sectionData] of Object.entries(data)) {
            const sectionContainer = createDetails(name);
            sectionContainer.classList.add('top-level-section');
            switch (name) {
                case 'settings':
                    const settingsForm = await this.createSettings(sectionData, sectionContainer);
                    this.sections[name] = settingsForm;
                    break;
                case 'actors':
                    const actorsForm = await this.createActors(sectionData, sectionContainer);
                    this.sections[name] = actorsForm;
                    break;
                case 'chains':
                    const actors = this.sections['actors'];
                    const info = new ActorsInfo(actors);
                    const chainsForm = await this.createChains(sectionData, sectionContainer, info);
                    sectionContainer.open = true;
                    this.sections[name] = chainsForm;
                    break;
                default:
                    const rawSection = new RawSection(sectionData);
                    sectionContainer.appendChild(rawSection.getElement());
                    this.sections[name] = rawSection;

                    const menu = new MenuItem(name, null, this.navigationArea);
                    menu.registerScrollHandler(rawSection.getElement());
            }
            this.container.appendChild(sectionContainer);
        }
        this.addActionBar();
    }

    async createSettings(sectionData, sectionContainer) {
        const menu = new MenuItem('Settings', null, this.navigationArea);

        const schema = await this.fetchJSON('/settings');
        const form = new SettingsForm(sectionData, schema);
        sectionContainer.appendChild(form.getElement());

        menu.registerScrollHandler(form.getElement());

        const rawInput = new RawSection(sectionData);
        sectionContainer.appendChild(rawInput.getElement());
        rawInput.getElement().style.display = 'none';
        registerFormContentMonitor(rawInput, form);

        return form;
    }

    async createActors(sectionData, sectionContainer) {
        const menu = new MenuItem('Actors', null, this.navigationArea);

        const actorsModel = await fetchJSON('/actors');
        const form = new ActorsForm(sectionData, actorsModel, menu);
        sectionContainer.appendChild(form.getElement());

        menu.registerScrollHandler(sectionContainer);

        const rawInput = new RawSection(sectionData);
        sectionContainer.appendChild(rawInput.getElement());
        rawInput.getElement().style.display = 'none';
        registerFormContentMonitor(rawInput, form);

        return form;
    }

    async createChains(sectionData, sectionContainer, info) {
        const menu = new MenuItem('Chains', null, this.navigationArea);

        const form = new ChainsForm(sectionData, menu, info);
        sectionContainer.appendChild(form.getElement());

        menu.registerScrollHandler(sectionContainer);

        const rawInput = new RawSection(sectionData);
        sectionContainer.appendChild(rawInput.getElement());
        rawInput.getElement().style.display = 'none';
        registerFormContentMonitor(rawInput, form);

        return form;
    }

    addActionBar() {
        const actionBar = document.createElement('div');
        actionBar.classList.add('action-bar');
        this.container.appendChild(actionBar);

        const checkButton = createButton('Check Config', this.makeSaveConfigCallback('check'), 'action-button');
        checkButton.title = 'Validate changes without applying them';
        actionBar.appendChild(checkButton);
        const reloadButton = createButton(
            'Save Changes and Reload',
            this.makeSaveConfigCallback('reload'),
            'action-button'
        );
        reloadButton.title = 'Save changes and restart avtdl';
        actionBar.appendChild(reloadButton);
    }

    makeSaveConfigCallback(mode = 'check') {
        const submitForm = () => {
            const data = {};
            for (const [name, section] of Object.entries(this.sections)) {
                data[name] = section.read();
            }
            this.submitConfig(data, mode);
        };
        return submitForm;
    }

    async submitConfig(data, mode) {
        this.messageArea.clear();
        try {
            const response = await fetch('/config?mode=' + mode, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data),
            });
            const responseText = await response.text();

            if (!response.ok) {
                if (response.status == 422) {
                    this.fillValidationError(responseText);
                } else {
                    throw new Error(responseText);
                }
            } else {
                if (mode == 'reload') {
                    this.messageArea.showMessage(responseText, 'success');
                    const motd_data = await fetchJSON('/motd', this.messageArea, 10);
                    if (motd_data) {
                        this.messageArea.showMessage(motd_data['motd'], 'info');
                        this.render();
                    }
                } else {
                    this.messageArea.showMessage(responseText, 'success');
                }
            }
        } catch (error) {
            console.error('Error saving config data:', error);
            this.messageArea.showMessage('Error saving data: ' + error.message, 'error');
        }
    }

    fillValidationError(errorResponseText) {
        const badResponse = 'failed to process server response to invalid config: ';
        const data = JSON.parse(errorResponseText);
        console.log(data);

        if (!(data instanceof Array)) {
            throw new Error(badResponse + errorResponseText);
        }
        for (const errorDetails of data) {
            const msg = errorDetails['msg'];
            const loc = errorDetails['loc'];
            if (!msg || !loc) {
                throw new Error(badResponse + errorResponseText);
            }
            if (loc[0] == 'settings' || loc[0] == 'actors' || loc[0] == 'chains') {
                const section = this.sections[loc[0]];
                const invalidFieldDiv = section.showError(loc, msg);
                const errorMessageText = 'Invalid input at [' + loc.join(' / ') + '] - ' + msg;
                this.messageArea.showMessage(errorMessageText, 'error', () => {
                    if (invalidFieldDiv) {
                        const sectionContainer = section.getElement().parentNode;
                        if (sectionContainer) {
                            sectionContainer.open = true;
                        }
                        openParentsDetails(invalidFieldDiv);
                        invalidFieldDiv.scrollIntoView(false);
                    }
                });
            }
        }
    }
}

function showMOTD(messageArea) {
    fetchJSON('/motd', this.messageArea, 10).then((motd_data) => {
        if (motd_data) {
            messageArea.showMessage(motd_data['motd'], 'info');
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const outputDiv = document.getElementById('output');
    const messageAreaDiv = document.getElementById('message-area');
    const navigationAreaDiv = document.getElementById('sidebar');

    const messageArea = new MessageArea(messageAreaDiv);
    const configForm = new ConfigEditor(outputDiv, messageArea, navigationAreaDiv);
    configForm.render();
});
