class ChainCard {
    constructor(info, getOwnName) {
        this.info = info;
        this.getName = getOwnName;

        this.parentContainer = createElement('div', 'card-container');
        this.container = createElement('div', 'chain-card');

        this.errorPlaceholder = addErrorPlaceholder(this.container);
        this.container.addEventListener('click', () => {
            clearInputError(this.container);
        });

        this.headerContainer = createElement('div', 'card-header-container', this.container);

        this.headerSelect = createElement('select', 'card-header', this.headerContainer);
        this.populateNestedDropdown(this.headerSelect, info.listTypes());
        this.headerSelect.value = null;
        this.headerSelect.onchange = () => {
            this.renderForHeader(this.headerSelect.value);
        };

        info.registerOnEntityChangeChangeHandler((actorName, oldName, NewName) => {
            this.handleEntityChange(actorName, oldName, NewName);
        });

        this.headerTooltip = null;

        this.itemsContainer = createElement('div', 'card-items', this.container);

        const addItemButton = createButton('[+]', () => this.addItem(), 'add-card-item-button');
        addItemButton.title = 'Insert empty line';
        this.container.appendChild(addItemButton);

        this.parentContainer.appendChild(this.container);
    }

    populateFlatDropdown(selectElement, values) {
        const selectedValue = selectElement.value;
        selectElement.innerHTML = '';
        values.forEach((value) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            selectElement.appendChild(option);
        });
        selectElement.value = selectedValue;
    }

    populateNestedDropdown(selectElement, values) {
        selectElement.innerHTML = '';
        for (const [group, options] of Object.entries(values)) {
            const optgroup = document.createElement('optgroup');
            optgroup.label = group;
            options.forEach((optionValue) => {
                const option = document.createElement('option');
                option.value = optionValue;
                option.textContent = optionValue;
                optgroup.appendChild(option);
            });
            selectElement.appendChild(optgroup);
        }
    }

    addItem(value = null) {
        const itemContainer = createElement('div', 'card-item-container');
        const header = this.headerSelect.value;

        let possibleValues = [];
        if (header) {
            possibleValues = this.info.listEntities(this.headerSelect.value);
        }

        const itemSelect = createElement('select', 'card-item-select', itemContainer);
        this.populateFlatDropdown(itemSelect, possibleValues);

        itemSelect.onchange = (event) => {
            this.updateItemHints(itemContainer, this.headerSelect.value, event.target.value);
        };

        const deleteButton = createButton(
            '[×]',
            () => {
                this.removeItem(itemContainer);
            },
            'inline-button'
        );
        deleteButton.title = 'Remove line';
        itemContainer.appendChild(deleteButton);

        const definitionButton = createButton(
            '⤴',
            () => {
                this.info.scrollTo(this.headerSelect.value, itemSelect.value);
            },
            'inline-button'
        );
        definitionButton.title = 'Go to entity definition';
        itemContainer.appendChild(definitionButton);

        const historyButton = createButton(
            'ⓘ',
            () => {
                this.info.historyView.showHistory(this.headerSelect.value, itemSelect.value, this.getName());
            },
            'inline-button'
        );
        itemContainer.appendChild(historyButton);
        historyButton.title = 'Show most recent records';

        if (value !== null) {
            itemSelect.value = value;
        } else {
            const usedValues = this.readItems();
            const freeValue = possibleValues.find((item) => !usedValues.includes(item)) || null;
            itemSelect.value = freeValue;
        }

        this.createItemHints(itemContainer);
        this.updateItemHints(itemContainer, this.headerSelect.value, itemSelect.value);

        this.itemsContainer.appendChild(itemContainer);
    }

    addItemHint(symbol, text, className, container) {
        const hint = createDefinition(symbol, text);
        hint.classList.add(className);
        hint.style.display = 'none';
        container.insertBefore(hint, container.firstChild);
    }

    createItemHints(itemContainer) {
        let text =
            'This entity has "consume_record" option enabled. ' +
            'It will not pass any incoming records down the chain. ' +
            'It may, however, produce records itself.';
        const hintConsumeRecord = this.addItemHint('⤓', text, 'hint-consume-record', itemContainer);

        text =
            'This entity has "reset_origin" option enabled. ' +
            'When used it multiple chains, it will pass incoming records ' +
            'from any of them to all of them.';
        const hintResetOrigin = this.addItemHint('⤋', text, 'hint-reset-origin', itemContainer);

        text =
            'This card lists multiple entities while being in the middle of the chain. ' +
            'Incoming records are fed into each of then in parallel, ' +
            'and the records each of them produce are passed down the chain.';
        const hintDuplicate = this.addItemHint('⚬', text, 'hint-duplicate', itemContainer);
    }

    showItemHint(className, hintContainer, show = true) {
        let hint = hintContainer.querySelector('.' + className);
        if (!hint) {
            return;
        }
        changeElementVisibility(hint, show);
    }

    cardPositionChanged(atEdgeOfChain = true) {
        const cardsContainers = Array.from(this.itemsContainer.children);
        cardsContainers.forEach((itemContainer) => {
            this.showItemHint('hint-duplicate', itemContainer, !atEdgeOfChain && cardsContainers.length > 1);
        });
    }

    updateItemHints(itemContainer, header, value) {
        const showConsumeRecord = this.info.getConsumeRecord(header, value) === true;
        this.showItemHint('hint-consume-record', itemContainer, showConsumeRecord);

        const showResetOrigin = this.info.getResetOrigin(header, value) === true;
        this.showItemHint('hint-reset-origin', itemContainer, showResetOrigin);
    }

    removeItem(itemContainer) {
        this.itemsContainer.removeChild(itemContainer);
    }

    renderForHeader(newHeaderValue) {
        this.itemsContainer.innerHTML = '';
        this.addItem();

        this.container.classList = ['chain-card'];
        const newType = this.info.actorType(newHeaderValue);
        if (newType) {
            this.container.classList.add(getActorTypeBgClass(newType));
        }

        if (this.headerTooltip) {
            this.headerContainer.removeChild(this.headerTooltip);
        }
        const description = this.info.listInfo(this.headerSelect.value);
        if (description) {
            this.headerTooltip = createTooltip(description);
            this.headerContainer.insertBefore(this.headerTooltip, this.headerSelect.nextSibling);
        }
    }

    handleEntityChange(actorName, oldName, newName) {
        if (actorName != this.getActorName()) {
            return;
        }
        const data = this.readItems();
        const updatedData = [];
        for (const entityName of data) {
            if (entityName == oldName) {
                if (newName !== null) {
                    //entity got renamed
                    updatedData.push(newName);
                }
            } else {
                updatedData.push(entityName);
            }
        }
        this.fill({ header: actorName, items: updatedData });
    }

    readItems() {
        const items = [];
        const itemsSelectors = this.itemsContainer.querySelectorAll('select');
        for (const item of itemsSelectors) {
            if (item.value) {
                items.push(item.value);
            }
        }
        return items;
    }

    read() {
        const data = {};
        data[this.headerSelect.value] = this.readItems();
        return data;
    }

    fill(data) {
        this.headerSelect.value = data.header;
        this.renderForHeader(data.header);

        this.itemsContainer.innerHTML = '';
        data.items.forEach((item) => {
            this.addItem(item);
        });
    }

    getActorName() {
        return this.headerSelect.value || null;
    }

    isEmpty() {
        return !this.headerSelect.value || !this.readItems();
    }

    getElement() {
        return this.parentContainer;
    }

    showError(path, message) {
        if (path instanceof Array) {
            return showInputError(this.container, message);
        }
        return null;
    }
}

class ChainSection {
    constructor(name, data, info) {
        this.name = name;
        this.info = info;
        this._menu = null;
        this.container = createElement('div');
        this.container.classList.add('chain-section');
        this.header = createElement('div', 'chain-header', this.container);
        this.nameContainer = createElement('div', 'chain-name', this.header);
        this.nameContainer.innerText = name;

        addErrorPlaceholder(this.container);
        this.container.addEventListener('click', () => {
            clearInputError(this.container);
        });

        this.cards = this.generateCards(data);
        observeChildMutations(this.container, () => this.handleReordering());
    }

    isEmpty() {
        return !this.name || !this.cards;
    }

    getMenu() {
        return this._menu;
    }

    setMenu(menuItem) {
        this._menu = menuItem;
    }

    getName() {
        return this.name;
    }

    rename(newName) {
        this.name = newName;
        this.nameContainer.textContent = newName;
    }

    getHeader() {
        return this.header;
    }

    makeAddButton(referenceCard) {
        const addButton = createButton(
            '[+]',
            () => {
                this.addEmptyCard(referenceCard);
            },
            'card-button'
        );
        addButton.classList.add('add-card-button');
        addButton.title = 'Insert new empty card after this one';
        return addButton;
    }

    generateCards(data) {
        let cards = [];
        if (data) {
            for (const cardData of data) {
                for (const [actorName, entities] of Object.entries(cardData)) {
                    const card = this.generateCard(actorName, entities);
                    cards.push(card);
                    this.container.appendChild(card.getElement());
                }
            }
        }
        return cards;
    }

    generateCard(actorName, entities, referenceNode = null) {
        const card = new ChainCard(this.info, () => {
            return this.getName();
        });
        if (actorName && entities) {
            card.fill({ header: actorName, items: entities });
        }

        const cardControls = createElement('div', 'card-controls');
        const deleteButton = createButton(
            '[×]',
            () => {
                this.deleteCard(card);
            },
            'card-button'
        );
        deleteButton.title = 'Delete card';
        cardControls.appendChild(deleteButton);

        const backwardsButton = createButton(
            '[⇧]',
            () => {
                this.moveCard(card, true);
            },
            'card-button'
        );
        backwardsButton.title = 'Move up';
        cardControls.appendChild(backwardsButton);

        const forwardButton = createButton(
            '[⇩]',
            () => {
                this.moveCard(card, false);
            },
            'card-button'
        );
        forwardButton.title = 'Move down';
        cardControls.appendChild(forwardButton);

        const addButton = this.makeAddButton(card);
        cardControls.appendChild(addButton);

        const cardContainer = card.getElement();
        cardContainer.appendChild(cardControls);

        return card;
    }

    moveCard(card, backwards = false) {
        const index = this.cards.indexOf(card);
        if (index == -1 || (index == 0 && backwards) || (index == this.cards.length - 1 && !backwards)) {
            return;
        }

        this.container.removeChild(card.getElement());
        this.cards.splice(index, 1);

        const newIndex = backwards ? index - 1 : index + 1;
        const neighbour = this.cards[newIndex] && this.cards[newIndex].getElement();
        this.cards.splice(newIndex, 0, card);

        this.container.insertBefore(card.getElement(), neighbour);
    }

    addEmptyCard(anchorCard) {
        const card = this.generateCard();
        card.addItem();
        if (!anchorCard) {
            this.container.appendChild(card.getElement());
            this.cards.push(card);
        } else {
            this.container.insertBefore(card.getElement(), anchorCard.getElement().nextSibling);
            const position = this.cards.indexOf(anchorCard);
            this.cards.splice(position + 1, 0, card);
        }
    }

    deleteCard(card) {
        this.container.removeChild(card.getElement());
        this.cards = this.cards.filter((x) => x !== card);
        if (this.cards.length == 0) {
            this.addEmptyCard();
        }
    }

    handleReordering() {
        for (let i = 0; i < this.cards.length; i++) {
            const atEdgeOfChain = i == 0 || i == this.cards.length - 1;
            const card = this.cards[i];
            card.cardPositionChanged(atEdgeOfChain);
        }
    }

    getElement() {
        return this.container;
    }

    read() {
        const data = {};
        const cardsData = [];
        for (const card of this.cards) {
            if (!card.isEmpty()) {
                cardsData.push(card.read());
            }
        }
        data[this.name] = cardsData;
        return data;
    }

    showError(path, message) {
        if (path instanceof Array) {
            if (path.length == 0) {
                return showInputError(this.container, message);
            } else if (path.length > 0) {
                const name = path[0];
                if (name in this.cards) {
                    return this.cards[name].showError(path.slice(1), message);
                }
            }
        }
        return null;
    }
}

class ChainsForm {
    constructor(data, menu, info) {
        this.container = document.createElement('div');
        this.container.classList.add('chains-form');
        this.menu = menu;
        this.info = info;
        this.chains = new OrderedDict();

        for (const [name, chainElements] of Object.entries(data)) {
            this.addChain(name, chainElements);
        }

        this.addButton = createButton(
            '[Add]',
            () => {
                this.addChain();
            },
            'add-chain-button'
        );
        this.addButton.classList.add('add-button');
        this.addButton.title = 'Add new chain';
        this.container.appendChild(this.addButton);
    }

    chooseChainName(base = 'Chain') {
        let name = chooseNewName(base, this.chains);
        if (name === null) {
            getUserInput('Name for a new chain:')
                .then((newName) => {
                    name = newName;
                })
                .catch(() => (name = ''));
        }
        return name;
    }

    generateChain(name, data) {
        const chainSection = new ChainSection(name, data, this.info);
        if (!data || data.length == 0) {
            chainSection.addEmptyCard();
        }
        const sectionContainer = this.wrapChain(chainSection);
        return [chainSection, sectionContainer];
    }

    addChain(name, data, anchor = null) {
        name = name || this.chooseChainName();
        if (!name) {
            return;
        }
        const [chainSection, sectionContainer] = this.generateChain(name, data);
        this.chains.set(name, chainSection);
        this.container.insertBefore(sectionContainer, anchor || this.addButton);
    }

    wrapChain(chainSection) {
        const menuItem = new MenuItem(chainSection.name, this.menu);
        menuItem.registerScrollHandler(chainSection.getElement());
        chainSection.setMenu(menuItem);

        const chainContainer = document.createElement('div');
        chainContainer.className = 'chain';

        const buttonsContainer = createElement('div', 'chain-buttons', chainSection.getHeader());

        const renameButton = this.makeRenameButton(chainSection, menuItem);
        buttonsContainer.appendChild(renameButton);

        const copyButton = this.makeCopyButton(chainSection, chainContainer, menuItem);
        buttonsContainer.appendChild(copyButton);

        const leftButton = this.makeMoveButton(chainSection, chainContainer, menuItem, '[⇦', false);
        buttonsContainer.appendChild(leftButton);
        const rightButton = this.makeMoveButton(chainSection, chainContainer, menuItem, '⇨]', true);
        buttonsContainer.appendChild(rightButton);

        const deleteButton = this.makeDeleteButton(chainSection, chainContainer, menuItem);
        buttonsContainer.appendChild(deleteButton);

        chainContainer.appendChild(chainSection.getElement());

        return chainContainer;
    }

    makeRenameButton(chainSection, menuItem) {
        const checkName = (name) => {
            if (name in this.chains) {
                return `chain ${name} already exists`;
            }
            return null;
        };
        const renameButton = createButton(
            '[✎]',
            () => {
                getUserInput(`New name for "${chainSection.name}":`, chainSection.name, this.container, checkName).then(
                    (newName) => {
                        if (newName in this.chains) {
                            return;
                        }
                        delete this.chains[chainSection.name];
                        this.chains.set(newName, chainSection);
                        chainSection.rename(newName);
                        menuItem.rename(newName);
                    }
                );
            },
            'inline-button'
        );
        renameButton.title = 'Rename chain';
        return renameButton;
    }

    makeDeleteButton(chainSection, chainContainer, menuItem) {
        const deleteChain = () => this.deleteChain(chainSection, chainContainer, menuItem);
        const deleteButton = createButton('[×]', () => deleteChain(), 'inline-button');
        deleteButton.classList.add('delete-chain-button');
        deleteButton.title = 'Delete chain';
        return deleteButton;
    }

    makeCopyButton(chainSection, chainContainer, menuItem) {
        const copyChain = () => {
            const name = chainSection.getName();
            const newName = this.chooseChainName(name);
            const data = chainSection.read()[name];

            const [newChainSection, newChainContainer] = this.generateChain(newName, data);
            this.chains.insertAfter(name, newName, newChainSection);
            this.container.insertBefore(newChainContainer, chainContainer.nextSibling);

            const existingMenuElement = menuItem.getElement();
            const menuContainer = existingMenuElement.parentNode;
            const newMenuElement = newChainSection.getMenu().getElement();
            menuContainer.removeChild(newMenuElement);
            menuContainer.insertBefore(newMenuElement, existingMenuElement.nextSibling);
        };
        const copyButton = createButton('[⧉]', () => copyChain(), 'inline-button');
        copyButton.classList.add('copy-chain-button');
        copyButton.title = 'Duplicate chain';
        return copyButton;
    }

    makeMoveButton(chainSection, chainContainer, menuItem, symbol, forward) {
        const moveChain = () => {
            this.moveChain(chainSection, chainContainer, menuItem, forward);
        };
        const moveButton = createButton(symbol, moveChain, 'inline-button');
        moveButton.title = forward ? 'Move forward' : 'Move back';
        return moveButton;
    }

    moveChain(chainSection, chainContainer, menuItem, forward = true) {
        if (chainContainer === this.container.firstChild && !forward) {
            return;
        }
        if (chainContainer === this.addButton.previousSibling && forward) {
            return;
        }
        const step = forward ? 1 : -1;
        this.chains.move(chainSection.name, step);
        moveElement(chainContainer, forward);
        moveElement(menuItem.getElement(), forward);
    }

    deleteChain(chainSection, chainContainer, menuItem) {
        this.container.removeChild(chainContainer);
        delete this.chains[chainSection.name];
        menuItem.remove();
    }

    getElement() {
        return this.container;
    }

    read() {
        let data = {};
        for (const [name, chain] of this.chains) {
            if (!chain.isEmpty()) {
                data = { ...data, ...chain.read() };
            }
        }
        return data;
    }

    showError(path, message) {
        if (path instanceof Array) {
            if (path.length >= 2) {
                if (path[0] == 'chains') {
                    if (path[1] in this.chains) {
                        return this.chains.get(path[1]).showError(path.slice(2), message);
                    }
                }
            }
        }
    }
}
