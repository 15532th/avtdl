/**
 * @param {any[][]} arrays
 */
function zipLongest(...arrays) {
    const maxLength = Math.max(...arrays.map((arr) => arr.length));
    return Array.from({ length: maxLength }, (_, i) => arrays.map((arr) => arr[i] || null));
}

/**
 * Renders a table element with the provided header and row data.
 * @param {Array<Node>} headersNodes
 * @param {Array<Array<Node>>} rowsNodes
 * @returns {HTMLTableElement}
 */
function renderTable(headersNodes, rowsNodes) {
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    const tbody = document.createElement('tbody');

    const headerRow = document.createElement('tr');

    headersNodes.forEach((node) => {
        const th = document.createElement('th');
        th.appendChild(node);
        headerRow.appendChild(th);
    });

    thead.appendChild(headerRow);
    table.appendChild(thead);
    table.appendChild(tbody);

    rowsNodes.forEach((rowElements) => {
        const row = document.createElement('tr');
        rowElements.forEach((element) => {
            const cell = document.createElement('td');
            cell.appendChild(element);
            row.appendChild(cell);
        });

        tbody.appendChild(row);
    });

    return table;
}

/**
 * @param {string[]} headers
 * @param {string[]} tooltips
 * @param {string[][]} rows
 */
function renderClickableTable(headers, tooltips, rows) {
    const headersData = zipLongest(headers, tooltips);
    const headersNodes = Array.from(headersData, ([text, tooltip]) => {
        const node = createElement('div');
        node.innerText = text || '';
        if (tooltip) {
            node.title = tooltip;
        }
        return node;
    });
    const elements = Array.from(rows, (row) => {
        return Array.from(row, (item) => {
            const element = createElement('div', 'history-content');
            element.innerHTML = item;
            element.onclick = () => {
                element.classList.toggle('minified');
            };
            element.click();
            return element;
        });
    });
    const content = renderTable(headersNodes, elements);
    return content;
}

class HistoryView {
    /**
     * @param {HTMLElement} parent
     */
    constructor(parent) {
        this.parent = parent;
    }

    /**
     * @param {string} actor
     * @param {string} entity
     * @param {string} chain
     */
    async fetchHistory(actor, entity, chain) {
        const url = new URL('/history', window.location.origin);
        url.searchParams.append('actor', actor);
        url.searchParams.append('entity', entity);
        if (chain) {
            url.searchParams.append('chain', chain);
        }
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`got ${response.status} (${response.statusText}) when requesting ${url}`);
        }
        const data = await response.json();
        return data;
    }

    /**
     * @param {HTMLElement | undefined} container
     * @param {any} error
     */
    renderError(container, error) {
        const message = createElement('p', 'history-row', container);
        message.innerText = `Error fetching recent records: ${error}`;
    }

    /**
     * @param {Node} container
     * @param {string | null | undefined} headline
     * @param {{ [s: string]: string[][]; }} data
     */
    renderHistory(container, data, headline) {
        if (!data) {
            return;
        }
        for (const [title, lines] of Object.entries(data)) {
            const section = createDetails(title, null, headline);
            section.open = true;
            container.appendChild(section);
            if (lines.length == 0) {
                const message = createElement('div', 'history-blank', section);
                message.innerText = 'no records so far';
                continue;
            }
            const headers = ['Parsed', 'Origin', 'Chain', 'Record'];
            const tooltips = [
                'Time this record was parsed at',
                'Actor and entity this record has originated from',
                'Chain this record is associated with',
                'Record preview',
            ];
            lines.forEach((line) => {
                line[0] = new Date(line[0]).toLocaleString();
            })
            const content = renderClickableTable(headers, tooltips, lines);
            section.appendChild(content);
        }
    }

    /**
     * @param {string} actor
     * @param {string} entity
     */
    showHistory(actor, entity, chain = '') {
        const container = renderModal(this.parent);
        let title = `${actor} - ${entity}`;
        if (chain) {
            title += ` - ${chain}`;
        }
        this.fetchHistory(actor, entity, chain)
            .then((data) => {
                this.renderHistory(container, data, title);
            })
            .catch((error) => {
                this.renderError(container, error);
            });
    }
}

class TaskView {
    /**
     * @param {string?} actor
     */
    static async fetchData(actor) {
        const url = new URL('/tasks', window.location.origin);
        if (actor) {
            url.searchParams.append('actor', actor);
        }
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`got ${response.status} (${response.statusText}) when requesting ${url}`);
        }
        const data = await response.json();
        return data;
    }

    /**
     * @param {HTMLElement | undefined} container
     * @param {any} error
     */
    static renderError(container, error) {
        const message = createElement('p', 'history-row', container);
        message.innerText = `Error fetching tasks status: ${error}`;
    }

    /**
     * @param {Node} container
     * @param {{ [s: string]: any; } | ArrayLike<any>} data
     */
    static renderView(container, data) {
        if (!data) {
            return;
        }
        for (const [actorName, actorData] of Object.entries(data)) {
            const section = createDetails(actorName);
            section.open = true;
            container.appendChild(section);
            if (actorData.length == 0) {
                const message = createElement('div', 'history-blank', section);
                message.innerText = 'no tasks running';
                continue;
            }

            const content = renderClickableTable(actorData['headers'] || [], [], actorData['rows'] || []);
            section.appendChild(content);
        }
    }

    /**
     * @param {string} actor
     * @param {HTMLElement} parent
     */
    static showView(parent, actor = '') {
        const container = renderModal(parent);
        this.fetchData(actor)
            .then((data) => {
                this.renderView(container, data);
            })
            .catch((error) => {
                this.renderError(container, error);
            });
    }
}
