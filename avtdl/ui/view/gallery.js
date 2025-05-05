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

    if (embed.author) {
        const author = document.createElement('div');
        author.classList.add('embed-author');
        const authorIcon = renderEmbedIcon(embed.author.icon_url);
        author.appendChild(authorIcon);
        if (embed.author.name) {
            const authorName = renderMaybeLink(embed.author.name, embed.author.url);
            authorName.classList.add('embed-author-name');
            author.appendChild(authorName);
        }
        embedDiv.appendChild(author);
    }

    if (embed.title || embed.url) {
        const title = renderMaybeLink(embed.title, embed.url);
        title.classList.add('embed-title');
        embedDiv.appendChild(title);
    }

    if (embed.description) {
        const description = renderTextContent(embed.description);
        description.classList.add('embed-description');
        embedDiv.appendChild(description);
    }

    if (embed.image) {
        const image = createImage(embed.image.url, 'embed-image', embedDiv);
        image.onclick = (event) => {
            const modal = renderModal(embedDiv);
            modal.classList.add('fullsize-image-container');
            const fullImage = createImage(embed.image.url, 'fullsize-image', modal);
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
        embedDiv.appendChild(image);
    }

    if (embed.thumbnail && embed.thumbnail.url) {
        const thumbnail = document.createElement('img');
        thumbnail.classList.add('embed-thumbnail');
        thumbnail.src = embed.thumbnail.url;
        thumbnail.alt = embed.thumbnail.url;
        embedDiv.appendChild(thumbnail);
    }

    if (embed.fields && Array.isArray(embed.fields)) {
        const fieldsContainer = createElement('div', 'embed-fields');
        embed.fields.forEach((field) => {
            const fieldDiv = createElement('div', 'embed-field', fieldsContainer);
            const fieldName = createElement('div', 'embed-field-name', fieldDiv);
            const fieldValue = createElement('div', 'embed-field-value', fieldDiv);
            fieldName.textContent = field.name;
            fieldValue.textContent = field.value;
        });
        embedDiv.appendChild(fieldsContainer);
    }

    if (embed.timestamp) {
        const timestamp = document.createElement('div');
        timestamp.classList.add('embed-timestamp');
        timestamp.textContent = new Date(embed.timestamp).toLocaleString();
        embedDiv.appendChild(timestamp);
    }
    if (embed.footer) {
        const footer = document.createElement('div');
        footer.classList.add('embed-footer');

        const footerIcon = renderEmbedIcon(embed.footer.icon_url);
        footer.appendChild(footerIcon);

        const footerText = createElement('span');
        footerText.classList.add('embed-footer-text');
        footerText.textContent = embed.footer.text;
        footer.appendChild(footerText);

        embedDiv.appendChild(footer);
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
    card.classList.add('discord-card');

    if (message.embeds && message.embeds.length > 0) {
        message.embeds.forEach((embed) => {
            const embedDiv = renderEmbed(embed);
            card.appendChild(embedDiv);
        });
        card.appendChild(renderMessageTimestamp(message.embeds));
    }
    return card;
}

class Gallery {
    /**
     * @param {HTMLElement} container
     */
    constructor(container) {
        this.container = container;
        this.container.classList.add('gallery-container');
    }

    /**
     *
     * @param {object[]} data
     */
    render(data) {
        data.forEach((element) => {
            const card = renderGalleryCard(element);
            card.classList.add('gallery-card');
            this.container.appendChild(card);
        });
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
