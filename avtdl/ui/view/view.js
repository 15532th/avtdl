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

class ViewState {
    /**
     * @param {string} actor
     * @param {string?} view
     * @param {string?} entity
     */
    constructor(actor, view, entity) {
        this.defaultValue = true;
        this.storage = new DataStorage(`view:${actor}:${view}:${entity}`);
    }

    /**
     * @param {string} key
     * @param {boolean} newValue
     */
    setBoolValue(key, newValue) {
        this.storage.set(key, newValue ? '1' : '0');
    }

    /**
     * @param {string} key
     */
    getBoolValue(key) {
        const value = this.storage.get(key);
        if (value === null) {
            return this.defaultValue;
        }
        return value == '1';
    }

    get showImages() {
        return this.getBoolValue('showImages');
    }

    set showImages(newValue) {
        this.setBoolValue('showImages', newValue);
    }

    get gridView() {
        return this.getBoolValue('gridView');
    }

    set gridView(newValue) {
        this.setBoolValue('gridView', newValue);
    }

    get fullDescriptions() {
        return this.getBoolValue('fullDescription');
    }

    set fullDescriptions(newValue) {
        this.setBoolValue('fullDescription', newValue);
    }
}

class RecordsView {
    /**
     * @param {HTMLElement} container
     * @param {MessageArea} messageArea
     */
    constructor(container, controlsContainer, messageArea) {
        this.container = container;
        this.controlsContainer = controlsContainer;
        this.messageArea = messageArea;
        this.originalPageTitle = document.title;

        this.container.innerHTML = '';

        this.viewState = this.getViewState();

        const galleryContainer = createElement('div', 'gallery', this.container);
        this.gallery = new Gallery(galleryContainer);

        const pageContainer = createElement('div', 'pagination', this.container);
        this.pages = new Pagination(pageContainer);

        this.controls = new ViewControls(controlsContainer, this.gallery, this.pages, this.viewState, () => {
            this.render();
        });
    }

    /**
     * @param {string | URL} path
     */
    async fetchJSON(path, reportError = false) {
        const messageArea = reportError ? this.messageArea : undefined;
        return await fetchJSON(path, messageArea);
    }

    params() {
        const params = new URLSearchParams(window.location.search);
        const actor = params.get('actor');
        const view = params.get('view');
        const entity = params.get('entity');

        const pageParam = params.get('page');
        const pageValue = pageParam ? parseInt(pageParam, 10) : null;
        const page = pageValue ? pageValue.toString() : null;

        const perPage = params.get('size');

        return {
            actor: actor,
            view: view,
            entity: entity,
            page: page,
            size: perPage,
        };
    }

    /**
     * @param {URL} url
     * @param {{ [s: string]: string?; }} params
     */
    setUrlParams(url, params) {
        for (const [key, value] of Object.entries(params)) {
            if (value) {
                url.searchParams.set(key, value);
            }
        }
    }

    getViewState() {
        const params = this.params();
        return new ViewState(params.actor || 'missing', params.view, params.entity);
    }

    async render() {
        const params = this.params();

        document.title = this.originalPageTitle;

        if (!params.actor) {
            this.container.innerText = 'Select plugin from menu on the left.';
            return;
        }
        const url = new URL('/records', window.location.origin);
        this.setUrlParams(url, params);

        const data = await this.fetchJSON(url);
        if (data === null) {
            this.container.innerText = 'No data for current selection. Select plugin from menu on the left.';
            return;
        }

        this.gallery.render(
            data['records'],
            this.viewState.showImages,
            this.viewState.gridView,
            this.viewState.fullDescriptions
        );

        this.pages.render(data['current'], data['total'], window.location.pathname + window.location.search);

        this.controls.render();

        document.title = `${params.view || params.entity || ''} / ${params.actor} — avtdl`;
    }
}

/**
 * @param {Function} callback0
 * @param {Function} callback1
 * @param {string} img0
 * @param {string} img1
 * @param {string?} hint0
 * @param {string?} hint1
 * @param {boolean} initialState
 */
function renderToggleButton(callback0, callback1, img0, img1, hint0, hint1, initialState = false) {
    const button = document.createElement('button');
    button.type = 'button';
    const image = document.createElement('img');
    button.appendChild(image);
    let currentState = !initialState;
    const toggle = () => {
        currentState = !currentState;
        image.src = currentState ? img1 : img0;
        button.title = (currentState ? hint1 : hint0) || '';
        const callback = currentState ? callback1 : callback0;
        callback();
    };
    toggle();
    button.onclick = toggle;
    return button;
}

/**
 * @param {string} img
 * @param {string} img_inactive
 * @param {string?} url
 */
function renderNavigationButton(img, img_inactive, url) {
    const button = document.createElement('button');
    button.type = 'button';
    const image = document.createElement('img');
    button.appendChild(image);
    image.src = img;
    if (url) {
        button.onclick = () => {
            window.location.href = url;
        };
    } else {
        button.disabled = true;
        image.src = img_inactive;
    }
    return button;
}

class ViewControls {
    /**
     * @param {HTMLElement} parent
     * @param {Gallery} gallery
     * @param {Pagination} pagination
     * @param {ViewState} state
     * @param {() => void} refresh
     */
    constructor(parent, gallery, pagination, state, refresh) {
        this.parent = parent;
        this.gallery = gallery;
        this.pagination = pagination;
        this.state = state;
        this.refreshEventAdded = false;
        this._refresh = refresh;
        this._refreshOngoing = false;

        this.container = createElement('div', 'controls', parent);
    }

    refresh() {
        if (this._refreshOngoing) {
            return;
        }
        this._refreshOngoing = true;
        this._refresh();
        this._refreshOngoing = false;
    }

    render() {
        this.container.innerHTML = '';
        const viewGroup = this.createGroup([
            renderToggleButton(
                () => {
                    this.gallery.toggleView(false);
                    this.state.gridView = false;
                },
                () => {
                    this.gallery.toggleView(true);
                    this.state.gridView = true;
                },
                '/res/view-list.svg',
                '/res/view-grid.svg',
                'List/Grid view',
                'Grid/List view',
                this.state.gridView
            ),
            renderToggleButton(
                () => {
                    this.gallery.toggleImages(false);
                    this.state.showImages = false;
                },
                () => {
                    this.gallery.toggleImages(true);
                    this.state.showImages = true;
                },
                '/res/img-hide.svg',
                '/res/img-show.svg',
                'Display/Hide images',
                'Display/Hide images',
                this.state.showImages
            ),
            renderToggleButton(
                () => {
                    this.gallery.toggleDescription(false);
                    this.state.fullDescriptions = false;
                },
                () => {
                    this.gallery.toggleDescription(true);
                    this.state.fullDescriptions = true;
                },
                '/res/show-cut.svg',
                '/res/show-full.svg',
                'Hide/Expand descriptions',
                'Expand/Hide descriptions',
                this.state.fullDescriptions
            ),
        ]);
        const navigationGroup = this.createGroup([
            renderNavigationButton('/res/arrow-left.svg', '/res/arrow-left-stop.svg', this.pagination.previousPageUrl),
            createButton('🔄', () => this.refresh()),
            renderNavigationButton('/res/arrow-right.svg', '/res/arrow-right-stop.svg', this.pagination.nextPageUrl),
        ]);
        this.container.appendChild(viewGroup);
        this.container.appendChild(navigationGroup);
        if (!this.refreshEventAdded) {
            document.addEventListener('keydown', (event) => {
                if (event.key === 'F5') {
                    if (event.ctrlKey || event.shiftKey || event.altKey) {
                        return;
                    }
                    event.preventDefault();
                    if (!event.repeat) {
                        this.refresh();
                    }
                }
            });
            this.refreshEventAdded = true;
        }
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
    const viewDiv = document.getElementById('output');
    const messageAreaDiv = document.getElementById('message-area');
    const navigationAreaDiv = document.getElementById('sidebar');
    if (viewDiv == null || messageAreaDiv == null || navigationAreaDiv == null) {
        console.log('missing page elements to mount on');
        return;
    }
    const messageArea = new MessageArea(messageAreaDiv);

    const navbar = new Sidebar(navigationAreaDiv, messageArea);
    navbar.render();

    const view = new RecordsView(viewDiv, navigationAreaDiv, messageArea);
    view.render();
});
