/**
 * Escapes HTML special characters to prevent XSS.
 * @param {string} str - The string to escape.
 * @returns {string} The escaped string.
 */
function escapeHtml(str) {
    const div = document.createElement('div');
    div.innerText = str; // Use innerText to escape HTML
    return div.innerHTML;
}

/**
 * Converts a Markdown string to a rendered HTML element.
 * @param {string} markdown - The Markdown string to convert.
 * @returns {HTMLElement} The converted HTML element.
 */
function renderMarkdown(markdown) {
    const container = document.createElement('div');
    const lines = markdown.split('\n');
    lines.forEach((line) => {
        let htmlLine = escapeHtml(line);

        htmlLine = htmlLine.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        htmlLine = htmlLine.replace(/\*(.*?)\*/g, '<em>$1</em>');

        htmlLine = htmlLine.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank">$1</a>');
        htmlLine = htmlLine.replace(/(ftp:\/\/[^\s]+)/g, '<a href="$1" target="_blank">$1</a>');
        htmlLine = htmlLine.replace(/(www\.[^\s]+)/g, '<a href="https://$1" target="_blank">$1</a>');

        htmlLine = htmlLine.replace(/`(.*?)`/g, '<code>$1</code>');
        htmlLine = htmlLine.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');

        const lineElement = document.createElement('div');
        lineElement.innerHTML = htmlLine;
        container.appendChild(lineElement);
        // container.appendChild(document.createElement('br'));
    });

    return container;
}

/**
 * Renders a card from a Discord-like message object.
 * @param {Object} message - The message object.
 * @returns {HTMLElement} The rendered card as a div element.
 */
function renderDiscordCard(message) {
    // Create the main card div
    const card = document.createElement('div');
    card.classList.add('discord-card');

    // Handle embeds if they exist
    if (message.embeds && message.embeds.length > 0) {
        message.embeds.forEach((embed) => {
            const embedDiv = renderDiscordEmbed(embed);
            card.appendChild(embedDiv);
        });
    }

    return card;
}

/**
 * @param {object} embed
 */
function renderDiscordEmbed(embed) {
    const embedDiv = document.createElement('div');
    embedDiv.classList.add('embed');

    // Create the header for the card if author information is present
    if (embed.author) {
        const author = document.createElement('div');
        author.classList.add('card-header');
        let authorName;
        if (embed.author.name) {
            if (embed.author.url) {
                authorName = document.createElement('a');
                authorName.href = embed.author.url;
            } else {
                authorName = document.createElement('span');
            }
            authorName.classList.add('author-name');
            authorName.textContent = embed.author.name;
            author.appendChild(authorName);
        }

        // Append the header to the card
        embedDiv.appendChild(author);
    }
    // Set embed color if it exists
    if (embed.color) {
        embedDiv.style.borderLeft = `4px solid #${embed.color.toString(16)}`; // Convert color to hex
    }

    // Create title if it exists
    if (embed.title) {
        const title = document.createElement('h4');
        title.classList.add('embed-title');
        title.textContent = embed.title;

        // Create a link for the title if a URL is provided
        if (embed.url) {
            const titleLink = document.createElement('a');
            titleLink.href = embed.url;
            titleLink.target = '_blank'; // Open in a new tab
            titleLink.appendChild(title);
            embedDiv.appendChild(titleLink);
        } else {
            embedDiv.appendChild(title);
        }
    }

    // Create description if it exists
    if (embed.description) {
        const description = document.createElement('p');
        description.classList.add('embed-description');
        const content = renderMarkdown(embed.description);
        description.appendChild(content);
        embedDiv.appendChild(description);
    }

    // Create image if it exists
    if (embed.image) {
        const image = document.createElement('img');
        image.classList.add('embed-image');
        image.src = embed.image.url;
        image.alt = embed.image.url;
        embedDiv.appendChild(image);
    }

    // Create fields if they exist
    if (embed.fields) {
        embed.fields.forEach((field) => {
            const fieldDiv = document.createElement('div');
            fieldDiv.classList.add('embed-field');

            const fieldName = document.createElement('strong');
            fieldName.textContent = field.name;
            fieldDiv.appendChild(fieldName);

            const fieldValue = document.createElement('span');
            fieldValue.textContent = field.value;
            fieldValue.classList.add('embed-field-value');
            fieldDiv.appendChild(fieldValue);

            embedDiv.appendChild(fieldDiv);
        });
    }

    if (embed.timestamp) {
        const timestamp = document.createElement('div');
        timestamp.classList.add('timestamp');
        timestamp.textContent = new Date(embed.timestamp).toLocaleString();
        embedDiv.appendChild(timestamp);
    }

    // Create footer if it exists
    if (embed.footer) {
        const footer = document.createElement('div');
        footer.classList.add('embed-footer');
        footer.textContent = embed.footer.text;
        embedDiv.appendChild(footer);
    }

    return embedDiv;
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
            const card = renderDiscordCard(element);
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
