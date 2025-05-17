class Sidebar {
    /**
     * @param {HTMLElement} parent
     * @param {MessageArea} messageArea
     */
    constructor(parent, messageArea) {
        this.container = createElement('div', 'navigation', parent);
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

        this.container.innerHTML = '';

        const galleryContainer = createElement('div', 'gallery', this.container);
        this.gallery = new Gallery(galleryContainer);

        const pageContainer = createElement('div', 'pagination', this.container);
        this.pages = new Pagination(pageContainer);
    }

    /**
     * @param {string | URL} path
     */
    async fetchJSON(path, reportError = false) {
        const messageArea = reportError ? this.messageArea : undefined;
        return await fetchJSON(path, messageArea);
    }

    async render() {
        const params = new URLSearchParams(window.location.search);
        const actor = params.get('actor');
        const view = params.get('view');
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
        if (view) {
            url.searchParams.set('view', view);
        }        if (entity) {
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
        this.gallery.render(data['records']);
        this.pages.render(data['current'], data['total'], window.location.pathname + window.location.search);

        document.title = `${view || entity} / ${actor} — avtdl`;
    }
}

class ViewControls {
    /**
     * @param {HTMLElement} parent
     * @param {Gallery} gallery
     * @param {Pagination} pagination
     */
    constructor(parent, gallery, pagination) {
        this.container = parent;
        this.gallery = gallery;
        this.pagination = pagination;

        this.container = createElement('div', 'controls', parent);
    }

    render() {
        const viewGroup = this.createGroup([
            this.gallery.makeToggleViewButton(),
            this.gallery.makeToggleImagesButton(),
            this.gallery.makeToggleDescriptionButton(),
        ]);
        this.container.appendChild(viewGroup);
    }

    /**
     * @param {HTMLButtonElement[]} buttons
     */
    createGroup(buttons) {
        const groupContainer = createElement('div', 'controls-group');
        buttons.forEach((button) => {
            button.classList.add('controls-button');
            groupContainer.appendChild(button);
        });
        return groupContainer;
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

    const controls = new ViewControls(navigationAreaDiv, view.gallery, view.pages);
    controls.render();
});
