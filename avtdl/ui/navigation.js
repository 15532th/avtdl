class MenuItem {
    constructor(name, parent, container) {
        this.name = name;
        this.parent = parent || null;
        this.submenuItems = [];
        this.isHighlighted = false; // Track highlight state

        // Create the element and set its properties
        this.element = document.createElement('div');
        this.element.className = parent ? 'nested-menu-item' : 'top-level-menu-item';

        this.headerContainer = document.createElement('div');
        this.headerContainer.classList.add('menu-header');
        this.element.appendChild(this.headerContainer);

        // Create toggle button
        this.toggleButton = document.createElement('span');
        this.toggleButton.style.fontFamily = 'monospace';
        this.toggleButton.style.cursor = 'pointer';
        this.toggleButton.onclick = () => this.toggleSubmenu();

        this.text = document.createElement('span');
        this.text.classList.add('menu-item-text');
        this.text.textContent = name;
        this.text.style.cursor = 'pointer';

        // Highlight indicator
        this.highlightIndicator = document.createElement('span');
        this.highlightIndicator.textContent = '●'; // Red circle symbol
        this.highlightIndicator.style.color = 'red';
        this.highlightIndicator.style.display = 'none'; // Hidden by default

        this.submenuCount = document.createElement('span');
        this.submenuCount.classList.add('menu-item-count');
        this._disableCountUpdateCallback = null;

        // Append toggle button, highlight indicator, and name to the element
        this.headerContainer.appendChild(this.toggleButton);
        this.headerContainer.appendChild(this.highlightIndicator);
        this.headerContainer.appendChild(this.text);
        this.headerContainer.appendChild(this.submenuCount);

        // Create a container for nested items
        this.submenuContainer = document.createElement('div');
        this.submenuContainer.classList.add('menu-container');
        this.submenuContainer.style.display = 'none'; // Hide initially
        this.element.appendChild(this.submenuContainer);

        // If there's a parent, append the item to the parent's element
        if (this.parent) {
            this.parent.submenuItems.push(this);
            this.parent.submenuContainer.appendChild(this.element);
            this.parent.toggleSubmenu(this.parent.parent === null);
        } else if (container) {
            container.appendChild(this.element); // Append to the provided container for top-level items
        }
    }

    submenuIsOpen() {
        return this.submenuContainer.style.display !== 'none';
    }

    toggleSubmenu(open) {
        let isVisible = this.submenuIsOpen();
        if (typeof open === 'boolean') {
            isVisible = !open;
        }
        this.submenuContainer.style.display = isVisible ? 'none' : 'block'; // Toggle visibility
        this.toggleButton.textContent = isVisible ? '[+]' : '[-]'; // Change button text
    }

    highlight() {
        this.isHighlighted = true;
        this.highlightIndicator.style.display = 'inline'; // Show the highlight indicator
        this.submenuItems.forEach((item) => item.highlight()); // Highlight all children

        if (this.parent) {
            this.parent.highlight(); // Highlight parent if it exists
        }
    }

    clearHighlight() {
        this.isHighlighted = false;
        this.highlightIndicator.style.display = 'none'; // Hide the highlight indicator
        this.submenuItems.forEach((item) => item.clearHighlight()); // Clear highlight from children
    }

    updateSubmenuCount() {
        const count = this.submenuContainer.childNodes.length;
        if (!count) {
            this.submenuCount.innerText = '';
            return;
        }
        this.submenuCount.innerText = `[${count}]`;
    }

    showSubmenuCount(show = true) {
        if (show) {
            if (this._disableCountUpdateCallback instanceof Function) {
                return;
            }
            this._disableCountUpdateCallback = observeChildMutations(this.submenuContainer, () => {
                this.updateSubmenuCount();
            });
        } else {
            if (this._disableCountUpdateCallback instanceof Function) {
                this._disableCountUpdateCallback();
                this._disableCountUpdateCallback = null;
                this.headerContainer.innerText = '';
            }
        }
    }

    addSubmenu(name) {
        const newItem = new MenuItem({ name, parent: this });
        this.submenuContainer.appendChild(newItem.element);
        return newItem;
    }

    rename(newName) {
        this.text.textContent = newName;
    }

    remove() {
        if (this.parent) {
            this.parent.submenuItems = this.parent.submenuItems.filter((item) => item !== this);
        }
        this.element.remove();
    }

    getElement() {
        return this.element;
    }

    registerScrollHandler(targetElement) {
        // Register onClick handler to scroll the target element into view
        this.text.onclick = () => {
            scrollIntoView(targetElement);
        };
    }

    scrollTo() {
        if (this.text.onclick instanceof Function) {
            this.text.click();
        }
    }
}
