async function initializeTimezoneList() {
    if (document['TIMEZONES']) {
        return;
    }
    const timezones = await fetchJSON('/timezones');
    if (timezones instanceof Array) {
        document['TIMEZONES'] = timezones;
    }
}

/**
 * @param {any[] | RawSection} rawSection
 * @param {SettingsForm | ActorsForm | ChainsForm} form
 */
function registerFormContentMonitor(rawSection, form) {
    let updateInput = () => {
        const content = form.read();
        rawSection.fill(content);
    };
    form.getElement().addEventListener('input', updateInput);
    form.getElement().addEventListener('change', updateInput);
    form.getElement().addEventListener('click', updateInput);
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
    /**
     * @param {HTMLElement} container
     * @param {MessageArea} messageArea
     * @param {HTMLElement} navigationArea
     */
    constructor(container, messageArea, navigationArea) {
        this.container = container;
        this.navigationArea = navigationArea;
        this.messageArea = messageArea;
        this.sections = {};
    }

    async fetchJSON(path) {
        return await fetchJSON(path, this.messageArea);
    }

    /**
     * @param {string} title
     */
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

    /**
     * @param {any} sectionData
     * @param {HTMLDetailsElement} sectionContainer
     */
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

    /**
     * @param {any} sectionData
     * @param {HTMLDetailsElement} sectionContainer
     */
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

    /**
     * @param {any} sectionData
     * @param {HTMLDetailsElement} sectionContainer
     * @param {ActorsInfo} info
     */
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

        const tasksButton = createButton('Running tasks', () => {TaskView.showView(this.container)}, 'action-button');
        tasksButton.title = 'Show tasks currently running for active actors'
        actionBar.appendChild(tasksButton);

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

    /**
     * @param {{}} data
     * @param {string} mode
     */
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

    /**
     * @param {string} errorResponseText
     */
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

/**
 * @param {MessageArea} messageArea
 */
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
    if (outputDiv == null || messageAreaDiv == null || navigationAreaDiv == null) {
        console.log('missing page elements to mount on')
        return;
    }
    const messageArea = new MessageArea(messageAreaDiv);
    const configForm = new ConfigEditor(outputDiv, messageArea, navigationAreaDiv);
    configForm.render();
});
