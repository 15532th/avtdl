class Context {
    constructor(a = false, newLine = false) {
        this.a = a; // inside a link
        this.newLine = newLine; // current position is a beginning of new line
    }
}

/**
 * @param {string} text
 * @param {Context?} ctx
 */
function renderTextContent(text, ctx = null) {
    const currentFragment = document.createDocumentFragment();
    if (ctx === null) {
        ctx = new Context();
    }

    // Define the mapping of regex patterns to handlers
    const patterns = [
        {
            //escaped characters
            regex: /^\\([\\`*_{}\[\]()#+-.!])/,
            handler: (match, ctx) => {
                return document.createTextNode(match[1]);
            },
        },
        {
            // multiple newlines for paragraph
            regex: /^((?!\n\n).+)\n\n\n?/,
            handler: (match, ctx) => {
                const p = document.createElement('p');
                const content = renderTextContent(match[1], ctx);
                p.appendChild(content);
                return p;
            },
        },
        {
            // single newline for line break
            regex: /^\n/,
            handler: (match, ctx) => {
                const fragment = document.createDocumentFragment();
                const br = document.createElement('br');
                fragment.appendChild(br);
                return fragment;
            },
        },
        {
            //  triple backticks for ```code```
            regex: /^```((?:(?!```).)*)```/s,
            handler: (match, ctx) => {
                const pre = document.createElement('pre');
                const code = document.createElement('code');
                code.innerText = match[1];
                pre.appendChild(code);
                return pre;
            },
        },
        {
            // singular backticks for inline `code`
            regex: /^`(?!`)([^`]+)`(?!`)/,
            handler: (match, ctx) => {
                const code = document.createElement('code');
                code.innerText = match[1];
                return code;
            },
        },
        {
            // ![alt](src) for image link
            regex: /^!\[([^\]]*)\]\(([^\)]+)\)/,
            handler: (match, ctx) => {
                if (ctx.a) {
                    return document.createTextNode(match[1]);
                } else {
                    return renderLink(match[2], match[1]);
                }
            },
        },
        {
            // [text content](href) for link
            regex: /^\[([^\]]*)\]\(([^\)]+)\)/,
            handler: (match, ctx) => {
                ctx.a = true;
                const content = renderTextContent(match[1], ctx);
                ctx.a = false;
                const link = renderLink(match[2], null);
                link.textContent = '';
                link.appendChild(content);
                return link;
            },
        },
        {
            // link wrapped in ()
            regex: /^\((https?:\/\/[^\s]+)\)/,
            handler: (match, ctx) => {
                const fragment = document.createDocumentFragment();
                fragment.appendChild(document.createTextNode('('));
                fragment.appendChild(renderLink(match[1], match[1]));
                fragment.appendChild(document.createTextNode(')'));
                return fragment;
            },
        },
        {
            // plaintext link
            regex: /^https?:\/\/[^\s]+/,
            handler: (match, ctx) => {
                if (ctx.a) {
                    return document.createTextNode(match[0]);
                } else {
                    return renderLink(match[0], match[0]);
                }
            },
        },
        {
            // >blockquote
            regex: /^>(.*)\n/,
            handler: (match, ctx) => {
                if (ctx.a || !ctx.newLine) {
                    // not actually a blockquote, render consumed text normally
                    const contentWrapper = document.createDocumentFragment();
                    contentWrapper.appendChild(document.createTextNode('>'));
                    const content = renderTextContent(match[1], ctx);
                    contentWrapper.appendChild(content);
                    return contentWrapper;
                } else {
                    // actually a blockquote
                    const blockquote = document.createElement('blockquote');
                    const content = renderTextContent(match[1], ctx);
                    blockquote.appendChild(content);
                    return blockquote;
                }
            },
        },
        {
            // **bold**
            regex: /^\*\*(.*?)\*\*/,
            handler: (match, ctx) => {
                const fragment = document.createDocumentFragment();
                const strong = document.createElement('strong');
                strong.textContent = match[1];
                fragment.appendChild(strong);
                return fragment;
            },
        },
        {
            // *italic*
            regex: /^\*(.*?)\*/,
            handler: (match, ctx) => {
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
                    currentFragment.appendChild(document.createTextNode(buffer));
                    buffer = ''; // Clear the buffer
                }
                ctx.newLine = currentIndex == 0 || (currentIndex > 0 && text[currentIndex - 1] == '\n');
                const fragment = handler(match, ctx);
                currentFragment.appendChild(fragment);
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
        currentFragment.appendChild(document.createTextNode(buffer));
    }

    return currentFragment;
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
 * @param {boolean} loadImages
 */
function renderEmbed(embed, loadImages = true) {
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
        const descriptionContainer = createElement('div', 'embed-description', embedBody);
        const description = renderTextContent(embed.description);
        descriptionContainer.appendChild(description);
    }

    if (embed.image) {
        const image = createImage(embed.image.url, 'embed-image', embedBody, loadImages);
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
        createImage(embed.thumbnail.src, 'embed-thumbnail', embedBody, loadImages);
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
 * remove unnecessarily "url" field from embeds attaching additional images to the main one
 * @param {object} embed
 */
function stripImageUrl(embed) {
    const keys = Object.keys(embed);
    if (keys.length === 2 && keys.includes('image') && keys.includes('url')) {
        delete embed.url;
    }
}

/**
 * @param {Object} message - The message object.
 * @param {boolean} loadImages
 * @returns {HTMLElement} The rendered card as a div element.
 */
function renderGalleryCard(message, loadImages) {
    const card = document.createElement('div');
    card.classList.add('gallery-card');

    if (message.embeds && message.embeds.length > 0) {
        message.embeds.forEach((/** @type {any} */ embed) => {
            stripImageUrl(embed);
            const embedDiv = renderEmbed(embed, loadImages);
            card.appendChild(embedDiv);
        });
        card.appendChild(renderMessageTimestamp(message.embeds));
    }
    return card;
}

/**
 * Toggle css class on element according to value
 * @param {HTMLElement} element
 * @param {string | null} className0 set if value is false
 * @param {string | null} className1 set if value is true
 * @param {boolean} value
 */
function toggleClass(element, className0, className1, value) {
    if (value) {
        if (className0) element.classList.remove(className0);
        if (className1) element.classList.add(className1);
    } else {
        if (className1) element.classList.remove(className1);
        if (className0) element.classList.add(className0);
    }
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
    render(data, showImg = true, gridView = true, fullDescriptions = true) {
        this.container.innerHTML = '';
        const lastElementSerialized = JSON.stringify(this.lastElement);
        let gotNewCards = false;
        data.forEach((element) => {
            const card = renderGalleryCard(element, showImg);
            this.container.appendChild(card);

            if (gotNewCards) {
                highlightBackground(card);
            }
            if (this.lastElement && JSON.stringify(element) == lastElementSerialized) {
                card.scrollIntoView();
                gotNewCards = true;
            }
        });
        if (data && data.length > 0) {
            this.lastElement = data[data.length - 1];
        }
        this.toggleImages(showImg);
        this.toggleView(gridView);
        this.toggleDescription(fullDescriptions);
    }

    /**
     * load/unload images and thumbnails on all cards of the Gallery
     * @param {boolean} show
     */
    toggleImages(show) {
        toggleClass(this.container, 'gallery-container-hide-images', null, show);
        this.container.querySelectorAll('.embed-image, .embed-thumbnail').forEach((img) => {
            if (img instanceof HTMLImageElement) {
                toggleImageState(img, show);
            } else {
                console.log('toggleImages selector returned element that is not an image: ', img);
            }
        });
    }

    /**
     * @param {boolean} grid
     */
    toggleView(grid) {
        toggleClass(this.container, 'gallery-container-list', 'gallery-container-grid', grid);
    }

    /**
     * @param {boolean} full
     */
    toggleDescription(full) {
        toggleClass(this.container, 'gallery-container-clamp-description', null, full);
    }
}

class Pagination {
    /**
     * @param {HTMLElement} container
     */
    constructor(container) {
        this.container = container;
        this.keyboardEventAdded = false;
        this._nextPageUrl = null;
        this._previousPageUrl = null;
    }

    /**
     * @param {number} currentPage
     * @param {number} totalPages
     * @param {string} baseUrl
     */
    render(currentPage, totalPages, baseUrl) {
        this.container.innerHTML = '';

        const onFirstPage = currentPage <= 1;
        const previousPageUrl = this.getPageUrl(baseUrl, currentPage - 1);
        this._previousPageUrl = onFirstPage ? null : previousPageUrl;

        this.addPageLink('«', this.getPageUrl(baseUrl, 1), onFirstPage);
        this.addPageLink('‹', previousPageUrl, onFirstPage);

        const pageNumbers = this.getPageNumbers(currentPage, totalPages);
        pageNumbers.forEach((page) => {
            const pageUrl = this.getPageUrl(baseUrl, page);
            const pageLink = this.createPageLink(page.toString(), pageUrl);
            if (page === currentPage) {
                pageLink.classList.add('active');
            }
            if (page == totalPages || page == 1) {
                pageLink.classList.add('pagination-link-edge');
            }
            this.container.appendChild(pageLink);
        });

        const onLastPage = currentPage >= totalPages;
        const nextPageUrl = this.getPageUrl(baseUrl, currentPage + 1);
        this._nextPageUrl = onLastPage ? null : nextPageUrl;

        this.addPageLink('›', nextPageUrl, onLastPage);
        this.addPageLink('»', this.getPageUrl(baseUrl, totalPages), onLastPage);

        if (!this.keyboardEventAdded) {
            this.addKeyboardNavigation(onFirstPage ? null : previousPageUrl, onLastPage ? null : nextPageUrl);
            this.keyboardEventAdded = true;
        }
    }

    get nextPageUrl() {
        return this._nextPageUrl;
    }

    get previousPageUrl() {
        return this._previousPageUrl;
    }

    /**
     * @param {string | null} previousPageLink
     * @param {string | null} nextPageLink
     */
    addKeyboardNavigation(previousPageLink, nextPageLink) {
        document.addEventListener('keydown', (event) => {
            if (event.ctrlKey) {
                if (previousPageLink && event.key === 'ArrowLeft') {
                    window.location.href = previousPageLink;
                }
                if (nextPageLink && event.key === 'ArrowRight') {
                    window.location.href = nextPageLink;
                }
            }
        });
    }

    /**
     * @param {string} text
     * @param {string} url
     */
    addPageLink(text, url, disabled = false) {
        const link = this.createPageLink(text, url, disabled);
        this.container.appendChild(link);
    }

    /**
     * @param {string} text
     * @param {string} url
     * @returns {HTMLElement}
     */
    createPageLink(text, url, disabled = false) {
        if (disabled) {
            const span = document.createElement('span');
            span.textContent = text;
            span.classList.add('pagination-text');
            return span;
        } else {
            const link = document.createElement('a');
            link.textContent = text;
            link.classList.add('pagination-link');

            link.href = url;
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
