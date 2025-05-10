class Sidebar {
    /**
     * @param {HTMLElement} container
     * @param {MessageArea} messageArea
     */
    constructor(container, messageArea) {
        this.container = container;
        this.messageArea = messageArea;
    }

    async render() {
        const data = await fetchJSON('/viewable', this.messageArea);
        if (data) {
            this.renderMenuLevel(data, null, this.container);
        }
    }

    /**
     * @param {{ [s: string]: any; } | ArrayLike<any>} data
     * @param {MenuItem | null} parentMenu
     * @param {HTMLElement | undefined} container
     */
    renderMenuLevel(data, parentMenu, container) {
        for (const [name, value] of Object.entries(data)) {
            const menuItem = new MenuItem(name, parentMenu, container);

            if (typeof value === 'string') {
                const currentUrl = window.location.pathname;
                const targetUrl = `${currentUrl}?${value}`;
                menuItem.registerUrlHandler(targetUrl);
            } else if (typeof value === 'object' && value !== null) {
                this.renderMenuLevel(value, menuItem, undefined);
            }
        }
    }
}

class RecordsView {
    /**
     * @param {HTMLElement} container
     * @param {MessageArea} messageArea
     */
    constructor(container, messageArea) {
        this.container = container;
        this.messageArea = messageArea;
        this.originalPageTitle = document.title;
    }

    async fetchJSON(path, reportError = false) {
        const messageArea = reportError ? this.messageArea : undefined;
        return await fetchJSON(path, messageArea);
    }

    clear() {
        this.container.innerHTML = '';
    }

    async render() {
        const params = new URLSearchParams(window.location.search);
        const actor = params.get('actor');
        const entity = params.get('entity');

        const pageParam = params.get('page');
        const page = pageParam ? parseInt(pageParam, 10) : null;
        
        const perPage = params.get('size');

        document.title = this.originalPageTitle;
        
        if (!actor) {
            this.container.innerText = 'Select plugin from menu on the left.';
            return;
        }
        const url = new URL('/records', window.location.origin);
        url.searchParams.set('actor', actor);
        if (entity) {
            url.searchParams.set('entity', entity);
        }
        if (page) {
            url.searchParams.set('page', page.toString());
        }
        if (perPage) {
            url.searchParams.set('size', perPage);
        }

        const data = await this.fetchJSON(url);
        if (data === null) {
            this.container.innerText = 'Select plugin from menu on the left.';
            return;
        }
        this.clear();

        const galleryContainer = createElement('div', 'gallery', this.container);
        const gallery = new Gallery(galleryContainer);
        gallery.render(data['records']);

        const pageContainer = createElement('div', 'pagination', this.container);
        const pages = new Pagination(pageContainer);
        pages.render(data['current'], data['total'], window.location.pathname + window.location.search);

        document.title = `${entity} / ${actor} — avtdl`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const outputDiv = document.getElementById('output');
    const messageAreaDiv = document.getElementById('message-area');
    const navigationAreaDiv = document.getElementById('sidebar');
    if (outputDiv == null || messageAreaDiv == null || navigationAreaDiv == null) {
        console.log('missing page elements to mount on');
        return;
    }
    const messageArea = new MessageArea(messageAreaDiv);
    const navbar = new Sidebar(navigationAreaDiv, messageArea);
    navbar.render();
    const view = new RecordsView(outputDiv, messageArea);
    view.render();
});
