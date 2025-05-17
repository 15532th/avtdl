// /**
//  * @param {string} text
//  */
// function renderTextContent(text) {
//     const container = document.createElement('div');
//     const lines = text.split('\n');
//     lines.forEach((line) => {
//         const lineElement = createElement('span', undefined, container);
//         lineElement.innerText = line;
//         createElement('br', undefined, container);
//     });
//     return container;
// }

/**
 * @param {string} text
 */
function renderTextContent(text) {
    const div = document.createElement('div');

    // Define the mapping of regex patterns to handlers
    const patterns = [
        {
            regex: /^([^\n]+)\n\n+/,
            handler: (match) => {
                const p = document.createElement('p');
                const content = renderTextContent(match[1]);
                p.appendChild(content);
                return p;
            },
        },
        {
            regex: /^\n/,
            handler: (match) => {
                const fragment = document.createDocumentFragment();
                const br = document.createElement('br');
                fragment.appendChild(br);
                return fragment;
            },
        },
        {
            regex: /^\[([^\]]*)\]\(([^\)]+)\)/,
            handler: (match) => {
                return renderLink(match[2], match[1]);
            },
        },
        {
            // links wrapped in ()
            regex: /^\((https?:\/\/[^\s]+)\)/,
            handler: (match) => {
                const fragment = document.createDocumentFragment();
                fragment.appendChild(document.createTextNode('('));
                fragment.appendChild(renderLink(match[1], match[1]));
                fragment.appendChild(document.createTextNode(')'));
                return fragment;
            },
        },
        {
            regex: /^https?:\/\/[^\s]+/,
            handler: (match) => {
                return renderLink(match[0], match[0]);
            },
        },
        {
            regex: /^\*\*(.*?)\*\*/,
            handler: (match) => {
                const fragment = document.createDocumentFragment();
                const strong = document.createElement('strong');
                strong.textContent = match[1];
                fragment.appendChild(strong);
                return fragment;
            },
        },
        {
            regex: /^\*(.*?)\*/,
            handler: (match) => {
                const fragment = document.createDocumentFragment();
                const em = document.createElement('em');
                em.textContent = match[1];
                fragment.appendChild(em);
                return fragment;
            },
        },
    ];

    let currentIndex = 0;
    let buffer = '';

    while (currentIndex < text.length) {
        let matched = false;

        for (const { regex, handler } of patterns) {
            const match = text.slice(currentIndex).match(regex);
            if (match) {
                // Append buffered text if any
                if (buffer) {
                    div.appendChild(document.createTextNode(buffer));
                    buffer = ''; // Clear the buffer
                }
                const fragment = handler(match);
                div.appendChild(fragment);
                currentIndex += match[0].length;
                matched = true;
                break; // Exit the loop after a match
            }
        }

        // If no match was found, add the current character to the buffer
        if (!matched) {
            buffer += text[currentIndex];
            currentIndex++;
        }
    }

    // Append any remaining buffered text at the end
    if (buffer) {
        div.appendChild(document.createTextNode(buffer));
    }

    return div;
}

/**
 * @param {string} link
 * @param {string?} text
 */
function renderLink(link, text) {
    const element = document.createElement('a');
    element.rel = 'noreferrer';
    element.target = '_blank';
    element.href = link;
    element.textContent = text || link;
    return element;
}

/**
 * @param {string?} text
 * @param {string?} link
 */
function renderMaybeLink(text, link) {
    let element;
    if (link) {
        element = renderLink(link, text);
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
        this.lastElement = null;
    }

    /**
     *
     * @param {object[]} data
     */
    render(data) {
        this.container.innerHTML = '';
        data.forEach((element) => {
            const card = renderGalleryCard(element);
            this.container.appendChild(card);

            if (element == this.lastElement) {
                card.scrollIntoView();
            }
        });

        if (data && data.length > 0) {
            this.lastElement = data[data.length - 1];
        }
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
     * @param {HTMLElement} container
     */
    constructor(container) {
        this.container = container;
    }

    /**
     * @param {number} currentPage
     * @param {number} totalPages
     * @param {string} baseUrl
     */
    render(currentPage, totalPages, baseUrl) {
        this.container.innerHTML = '';

        const onFirstPage = currentPage <= 1;
        this.addPageLink('«', 1, baseUrl, onFirstPage);
        this.addPageLink('‹', currentPage - 1, baseUrl, onFirstPage);

        const pageNumbers = this.getPageNumbers(currentPage, totalPages);
        pageNumbers.forEach((page) => {
            const pageLink = this.createPageLink(page.toString(), page, baseUrl);
            if (page === currentPage) {
                pageLink.classList.add('active');
            }
            this.container.appendChild(pageLink);
        });

        const onLastPage = currentPage >= totalPages;
        this.addPageLink('›', currentPage + 1, baseUrl, onLastPage);
        this.addPageLink('»', totalPages, baseUrl, onLastPage);
    }

    /**
     * @param {string} text
     * @param {number} page
     * @param {string} baseUrl
     */
    addPageLink(text, page, baseUrl, disabled = false) {
        const link = this.createPageLink(text, page, baseUrl, disabled);
        this.container.appendChild(link);
    }

    /**
     * @param {string} text
     * @param {number} page
     * @param {string} baseUrl
     * @returns {HTMLElement}
     */
    createPageLink(text, page, baseUrl, disabled = false) {
        if (disabled) {
            const span = document.createElement('span');
            span.textContent = text;
            span.classList.add('pagination-text');
            return span;
        } else {
            const link = document.createElement('a');
            link.textContent = text;
            link.classList.add('pagination-link');

            link.href = this.getPageUrl(baseUrl, page);
            return link;
        }
    }

    /**
     * @param {string | URL} baseUrl
     * @param {number} page
     */
    getPageUrl(baseUrl, page) {
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
    getPageNumbers(currentPage, totalPages) {
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
