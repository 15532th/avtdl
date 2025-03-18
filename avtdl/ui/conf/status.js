/**
 * Renders a table element with the provided header and row data.
 * @param {Object<string, string>} headersData - Dictionary of header text and tooltip.
 * @param {Array<Array<Node>>} rowsData - Array of rows, with every row being array of cells contained by the row.
 * @returns {HTMLTableElement} - The rendered table element.
 */
function renderTable(headersData, rowsData) {
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    const tbody = document.createElement('tbody');

    const headerRow = document.createElement('tr');

    for (const [header, title] of Object.entries(headersData)) {
        const th = document.createElement('th');
        th.textContent = header;
        th.title = title;
        headerRow.appendChild(th);
    }

    thead.appendChild(headerRow);
    table.appendChild(thead);
    table.appendChild(tbody);

    rowsData.forEach((rowElements) => {
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

    renderPopup() {
        const background = createElement('div', 'modal-background', this.parent);
        const container = createElement('div', 'history-view', background);
        background.onclick = (event) => {
            if (event.target === background) {
                this.parent.removeChild(background);
            }
        };
        background.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                this.parent.removeChild(background);
            }
        });
        return container;
    }

    /**
     * @param {{ appendChild: (arg0: HTMLDetailsElement) => void; }} container
     * @param {{ [s: string]: any; } | ArrayLike<any>} data
     * @param {string | null | undefined} headline
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

            const headers = {
                Origin: 'Actor and entity this records has originated from',
                Chain: 'Chain this records is associated with',
                Record: 'Record preview',
            };
            const elements = Array.from(lines, (line) => {
                return Array.from(line, (item) => {
                    const element = createElement('div', 'history-content');
                    element.innerHTML = item;
                    element.onclick = () => {
                        element.classList.toggle('minified');
                    };
                    element.click();
                    return element;
                });
            });
            const content = renderTable(headers, elements);
            section.appendChild(content);
        }
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
     * @param {string} actor
     * @param {string} entity
     */
    showHistory(actor, entity, chain = '') {
        const container = this.renderPopup();
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
