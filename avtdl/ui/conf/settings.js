class SettingsForm {
    constructor(data, schema) {
        this.container = document.createElement('form');
        this.schema = flattenSchema(schema);
        this.settings = this.generateSettings(data);
    }
    generateSettings(data) {
        const settings = new Fieldset(this.schema, this.container);
        if (data) {
            settings.fill(data);
        }
        return settings;
    }

    getElement() {
        return this.container;
    }

    read() {
        return this.settings.read();
    }

    showError(path, message) {
        if (path instanceof Array) {
            if (path.length > 1) {
                if (path[0] == 'settings') {
                    return this.settings.showError(path.slice(1), message);
                }
            }
        }
    }
}
