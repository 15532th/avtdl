/**
 * @param {string} text
 */
function renderTextContent(text) {
    const container = document.createElement('div');
    const lines = text.split('\n');
    lines.forEach((line) => {
        const lineElement = createElement('span', undefined, container);
        lineElement.innerText = line;
        createElement('br', undefined, container);
    });
    return container;
}

/**
 * @param {string?} text
 * @param {string?} link
 */
function renderMaybeLink(text, link) {
    let element;
    if (link) {
        element = document.createElement('a');
        element.rel = 'noreferrer';
        element.target = '_blank';
        element.href = link;
        element.textContent = text || link;
    } else {
        element = document.createElement('span');
        element.textContent = text || '';
    }
    return element;
}

/**
 * @param {string?} url
 */
function renderEmbedIcon(url) {
    const container = document.createElement('div');
    container.classList.add('embed-icon-container');
    if (url) {
        const icon = createImage(url, 'embed-icon', container);
        icon.onerror = () => {
            icon.style.display = 'none';
        };
        const preview = createImage(url, 'embed-icon-preview', container);
    }
    return container;
}

/**
 * @param {object} embed
 */
function renderEmbed(embed) {
    const embedDiv = document.createElement('div');
    embedDiv.classList.add('embed-container');

    if (embed.color) {
        embedDiv.style.borderLeft = `4px solid #${embed.color.toString(16)}`;
    }

    const embedHeader = createElement('div', 'embed-header', embedDiv);

    if (embed.author) {
        const author = createElement('div', 'embed-author', embedHeader);
        const authorIcon = renderEmbedIcon(embed.author.icon_url);
        author.appendChild(authorIcon);
        if (embed.author.name) {
            const authorName = renderMaybeLink(embed.author.name, embed.author.url);
            authorName.classList.add('embed-author-name');
            author.appendChild(authorName);
        }
    }

    if (embed.title || embed.url) {
        const title = renderMaybeLink(embed.title, embed.url);
        title.classList.add('embed-title');
        embedHeader.appendChild(title);
    }

    const embedBody = createElement('div', 'embed-body', embedDiv);

    if (embed.description) {
        const description = renderTextContent(embed.description);
        description.classList.add('embed-description');
        embedBody.appendChild(description);
    }

    if (embed.image) {
        const image = createImage(embed.image.url, 'embed-image', embedBody);
        image.onclick = (event) => {
            const modal = renderModal(embedDiv);
            modal.classList.add('fullsize-image-container');
            createImage(embed.image.url, 'fullsize-image', modal);
        };
        if (embed.image._preview) {
            image.onmouseenter = () => {
                image.src = embed.image._preview;
            };
            image.onmouseleave = () => {
                image.src = embed.image.url;
            };
            image.onerror = () => {
                image.onmouseenter = null;
                image.onmouseleave = null;
            };
        }
    }

    if (embed.thumbnail && embed.thumbnail.url) {
        const thumbnail = document.createElement('img');
        thumbnail.classList.add('embed-thumbnail');
        thumbnail.src = embed.thumbnail.url;
        thumbnail.alt = embed.thumbnail.url;
        embedBody.appendChild(thumbnail);
    }

    if (embed.fields && Array.isArray(embed.fields)) {
        const fieldsContainer = createElement('div', 'embed-fields');
        embed.fields.forEach((/** @type {{ name: string | null; value: string | null; }} */ field) => {
            const fieldDiv = createElement('div', 'embed-field', fieldsContainer);
            const fieldName = createElement('div', 'embed-field-name', fieldDiv);
            const fieldValue = createElement('div', 'embed-field-value', fieldDiv);
            fieldName.textContent = field.name;
            fieldValue.textContent = field.value;
        });
        embedDiv.appendChild(fieldsContainer);
    }

    const embedFooter = createElement('div', 'embed-footer', embedDiv);

    if (embed.footer) {
        const footer = document.createElement('div');
        footer.classList.add('embed-footer-content');

        const footerIcon = renderEmbedIcon(embed.footer.icon_url);
        footer.appendChild(footerIcon);

        const footerText = createElement('span');
        footerText.classList.add('embed-footer-text');
        footerText.textContent = embed.footer.text;
        footer.appendChild(footerText);

        embedFooter.appendChild(footer);
    }
    if (embed.timestamp) {
        const timestamp = document.createElement('div');
        timestamp.classList.add('embed-timestamp');
        timestamp.textContent = new Date(embed.timestamp).toLocaleString();
        embedFooter.appendChild(timestamp);
    }

    return embedDiv;
}

/**
 * Return first valid _timestamp field from embeds in the message
 * @param {any[]} embeds
 */
function getMessageTimestamp(embeds) {
    let messageTimestamp = null;
    embeds.forEach((embed) => {
        if (embed['_timestamp']) {
            messageTimestamp = embed['_timestamp'];
        }
    });
    if (!messageTimestamp) {
        return null;
    }
    return messageTimestamp;
}

/**
 * Render element containing timestamp. If messageTimestamp is missing or invalid, render empty element
 * @param {any[]} embeds
 */
function renderMessageTimestamp(embeds) {
    const messageTimestamp = getMessageTimestamp(embeds);
    const element = document.createElement('div');
    element.classList.add('gallery-card-timestamp');
    if (messageTimestamp) {
        const ts = new Date(messageTimestamp).toLocaleString();
        if (ts != 'Invalid Date') {
            element.innerText = `Parsed ${ts}`;
        }
    }
    return element;
}

/**
 * @param {Object} message - The message object.
 * @returns {HTMLElement} The rendered card as a div element.
 */
function renderGalleryCard(message) {
    const card = document.createElement('div');
    card.classList.add('gallery-card');

    if (message.embeds && message.embeds.length > 0) {
        message.embeds.forEach((/** @type {any} */ embed) => {
            const embedDiv = renderEmbed(embed);
            card.appendChild(embedDiv);
        });
        card.appendChild(renderMessageTimestamp(message.embeds));
    }
    return card;
}

/**
 * @param {Function} callback0
 * @param {Function} callback1
 * @param {string} text0
 * @param {string} text1
 * @param {string?} hint0
 * @param {string?} hint1
 * @param {boolean} initialState
 */
function renderToggleButton(callback0, callback1, text0, text1, hint0, hint1, initialState = false) {
    const button = document.createElement('button');
    button.type = 'button';
    let currentState = !initialState;
    const toggle = () => {
        currentState = !currentState;
        button.innerText = currentState ? text1 : text0;
        button.title = (currentState ? hint1 : hint0) || '';
        const callback = currentState ? callback1 : callback0;
        callback();
    };
    toggle();
    button.onclick = toggle;
    return button;
}

/**
 * @param {HTMLElement} element
 * @param {string | null} className0
 * @param {string | null} className1
 * @param {string} text0
 * @param {string} text1
 * @param {string | null} hint0
 * @param {string | null} hint1
 */
function renderStyleToggleButton(element, className0, className1, text0, text1, hint0, hint1, initialState = false) {
    return renderToggleButton(
        () => {
            if (className1) element.classList.remove(className1);
            if (className0) element.classList.add(className0);
        },
        () => {
            if (className0) element.classList.remove(className0);
            if (className1) element.classList.add(className1);
        },
        text0,
        text1,
        hint0,
        hint1,
        initialState
    );
}

class Gallery {
    /**
     * @param {HTMLElement} container
     */
    constructor(container) {
        this.container = container;
        this.container.classList.add('gallery-container');
        this.container.classList.add('gallery-container-grid');
    }

    /**
     *
     * @param {object[]} data
     */
    render(data) {
        data.forEach((element) => {
            const card = renderGalleryCard(element);
            this.container.appendChild(card);
        });
    }

    makeToggleViewButton() {
        return renderStyleToggleButton(
            this.container,
            'gallery-container-grid',
            'gallery-container-list',
            '▦',
            '▤',
            'Grid/List view',
            'List/Grid view'
        );
    }

    makeToggleImagesButton() {
        return renderStyleToggleButton(
            this.container,
            null,
            'gallery-container-hide-images',
            '🖼',
            '🖾',
            'Display/Hide images',
            'Display/Hide images'
        );
    }

    makeToggleDescriptionButton() {
        return renderStyleToggleButton(
            this.container,
            null,
            'gallery-container-clamp-description',
            '☐',
            '⬒',
            'Expand/Hide descriptions',
            'Hide/Expand descriptions'
        );
    }
}

class Pagination {
    /**
     * @param {HTMLElement} container - The DOM element to mount the pagination on.
     */
    constructor(container) {
        this.container = container;
    }

    /**
     * Renders the pagination section.
     * @param {number} currentPage - The current page number.
     * @param {number} totalPages - The total number of pages.
     * @param {string} baseUrl - The base URL to use for constructing links.
     */
    render(currentPage, totalPages, baseUrl) {
        this.container.innerHTML = '';

        const fragment = document.createDocumentFragment();

        if (currentPage > 1) {
            const firstLink = this.createPageLink('«', 1, baseUrl);
            fragment.appendChild(firstLink);
        } else {
            fragment.appendChild(this.createPlainText('«'));
        }

        if (currentPage > 1) {
            const prevLink = this.createPageLink('‹', currentPage - 1, baseUrl);
            fragment.appendChild(prevLink);
        } else {
            fragment.appendChild(this.createPlainText('‹'));
        }

        const pageLinks = this.getPageLinks(currentPage, totalPages);
        pageLinks.forEach((page) => {
            const pageLink = this.createPageLink(page.toString(), page, baseUrl);
            if (page === currentPage) {
                pageLink.classList.add('active');
            }
            fragment.appendChild(pageLink);
        });

        if (currentPage < totalPages) {
            const nextLink = this.createPageLink('›', currentPage + 1, baseUrl);
            fragment.appendChild(nextLink);
        } else {
            fragment.appendChild(this.createPlainText('›'));
        }

        if (currentPage < totalPages) {
            const lastLink = this.createPageLink('»', totalPages, baseUrl);
            fragment.appendChild(lastLink);
        } else {
            fragment.appendChild(this.createPlainText('»'));
        }

        this.container.appendChild(fragment);
    }

    /**
     * Creates a link element for a given page.
     * @param {string} text - The text to display for the link.
     * @param {number} page - The page number for the link.
     * @param {string} baseUrl - The base URL to use for the link.
     * @returns {HTMLAnchorElement} The created link element.
     */
    createPageLink(text, page, baseUrl) {
        const link = document.createElement('a');
        link.textContent = text;
        link.href = this.constructUrl(baseUrl, page);
        link.classList.add('pagination-link');
        return link;
    }

    /**
     * Creates a plain text element for non-clickable items.
     * @param {string} text - The text to display.
     * @returns {HTMLElement} The created plain text element.
     */
    createPlainText(text) {
        const span = document.createElement('span');
        span.textContent = text;
        span.classList.add('pagination-text');
        return span;
    }

    /**
     * Constructs the full URL with the page query parameter.
     * @param {string} baseUrl - The base URL.
     * @param {number} page - The page number to append as a query parameter.
     * @returns {string} The constructed URL.
     */
    constructUrl(baseUrl, page) {
        const url = new URL(baseUrl, window.location.origin);
        url.searchParams.set('page', page.toString());
        return url.toString();
    }

    /**
     * Gets an array of page numbers to display.
     * @param {number} currentPage - The current page number.
     * @param {number} totalPages - The total number of pages.
     * @returns {number[]} An array of page numbers to display.
     */
    getPageLinks(currentPage, totalPages) {
        const pageLinks = [];
        const maxPagesToShow = 7;
        const halfRange = Math.floor(maxPagesToShow / 2);

        let startPage = Math.max(1, currentPage - halfRange);
        let endPage = Math.min(totalPages, currentPage + halfRange);

        // Adjust the start and end pages if there are not enough pages to show
        if (endPage - startPage < maxPagesToShow - 1) {
            if (startPage === 1) {
                endPage = Math.min(totalPages, startPage + maxPagesToShow - 1);
            } else {
                startPage = Math.max(1, endPage - maxPagesToShow + 1);
            }
        }

        for (let i = startPage; i <= endPage; i++) {
            pageLinks.push(i);
        }

        return pageLinks;
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
            this.gallery.makeToggleDescriptionButton()
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

    /**
     * @param {string | null} text
     * @param {(event: MouseEvent | undefined) => void} callback
     */
    createButton(text, callback) {
        const button = createElement('button', 'controls-button');
        button.textContent = text;
        button.onclick = callback;
        return button;
    }
}
