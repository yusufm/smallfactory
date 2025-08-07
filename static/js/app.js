// smallfactory Web Interface JavaScript

// Global app configuration
const SF_APP = {
    apiBase: '/api',
    version: '1.0'
};

// Utility functions
const Utils = {
    // Show loading state on element
    showLoading: function(element) {
        element.classList.add('loading');
        element.disabled = true;
    },
    
    // Hide loading state on element
    hideLoading: function(element) {
        element.classList.remove('loading');
        element.disabled = false;
    },
    
    // Show toast notification
    showToast: function(message, type = 'info') {
        // Create toast element
        const toast = document.createElement('div');
        toast.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
        toast.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
        toast.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        document.body.appendChild(toast);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 5000);
    },
    
    // Format number with commas
    formatNumber: function(num) {
        return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    },
    
    // Debounce function
    debounce: function(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
};

// API helper functions
const API = {
    // Generic API request
    request: async function(endpoint, options = {}) {
        const url = `${SF_APP.apiBase}${endpoint}`;
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json'
            }
        };
        
        const config = { ...defaultOptions, ...options };
        
        try {
            const response = await fetch(url, config);
            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.error || 'API request failed');
            }
            
            return data;
        } catch (error) {
            console.error('API Error:', error);
            throw error;
        }
    },
    
    // Get all inventory items
    getInventory: function() {
        return this.request('/inventory');
    },
    
    // Get specific inventory item
    getInventoryItem: function(sku) {
        return this.request(`/inventory/${sku}`);
    },
    
    // Add new inventory item
    addInventoryItem: function(item) {
        return this.request('/inventory', {
            method: 'POST',
            body: JSON.stringify(item)
        });
    },
    
    // Update inventory item
    updateInventoryItem: function(sku, updates) {
        return this.request(`/inventory/${sku}`, {
            method: 'PUT',
            body: JSON.stringify(updates)
        });
    },
    
    // Adjust inventory quantity
    adjustInventoryQuantity: function(sku, delta) {
        return this.request(`/inventory/${sku}/adjust`, {
            method: 'POST',
            body: JSON.stringify({ delta })
        });
    },
    
    // Delete inventory item
    deleteInventoryItem: function(sku) {
        return this.request(`/inventory/${sku}`, {
            method: 'DELETE'
        });
    }
};

// Enhanced search functionality
const Search = {
    init: function() {
        const searchInput = document.getElementById('searchInput');
        if (searchInput) {
            searchInput.addEventListener('keyup', Utils.debounce(this.performSearch, 300));
        }
    },
    
    performSearch: function() {
        const searchTerm = document.getElementById('searchInput').value.toLowerCase();
        const table = document.getElementById('inventoryTable');
        
        if (!table) return;
        
        const rows = table.getElementsByTagName('tbody')[0].getElementsByTagName('tr');
        let visibleCount = 0;
        
        for (let i = 0; i < rows.length; i++) {
            const row = rows[i];
            const text = row.textContent.toLowerCase();
            const isVisible = text.includes(searchTerm);
            
            row.style.display = isVisible ? '' : 'none';
            if (isVisible) visibleCount++;
        }
        
        // Update search results count if element exists
        const resultsCount = document.getElementById('searchResults');
        if (resultsCount) {
            resultsCount.textContent = `Showing ${visibleCount} of ${rows.length} items`;
        }
    }
};

// Form validation and enhancement
const Forms = {
    init: function() {
        // Add validation to all forms
        const forms = document.querySelectorAll('form');
        forms.forEach(form => {
            form.addEventListener('submit', this.validateForm);
        });
        
        // Add auto-formatting to number inputs
        const numberInputs = document.querySelectorAll('input[type="number"]');
        numberInputs.forEach(input => {
            input.addEventListener('blur', this.formatNumberInput);
        });
        
        // Add character counters to textareas
        const textareas = document.querySelectorAll('textarea');
        textareas.forEach(textarea => {
            this.addCharacterCounter(textarea);
        });
    },
    
    validateForm: function(event) {
        const form = event.target;
        const requiredFields = form.querySelectorAll('[required]');
        let isValid = true;
        
        requiredFields.forEach(field => {
            if (!field.value.trim()) {
                field.classList.add('is-invalid');
                isValid = false;
            } else {
                field.classList.remove('is-invalid');
            }
        });
        
        if (!isValid) {
            event.preventDefault();
            Utils.showToast('Please fill in all required fields', 'danger');
        }
    },
    
    formatNumberInput: function(event) {
        const input = event.target;
        const value = parseInt(input.value);
        
        if (!isNaN(value)) {
            input.value = value;
        }
    },
    
    addCharacterCounter: function(textarea) {
        const maxLength = textarea.getAttribute('maxlength');
        if (!maxLength) return;
        
        const counter = document.createElement('small');
        counter.className = 'form-text text-muted character-counter';
        textarea.parentNode.appendChild(counter);
        
        const updateCounter = () => {
            const remaining = maxLength - textarea.value.length;
            counter.textContent = `${remaining} characters remaining`;
            counter.className = `form-text ${remaining < 10 ? 'text-danger' : 'text-muted'} character-counter`;
        };
        
        textarea.addEventListener('input', updateCounter);
        updateCounter();
    }
};

// Keyboard shortcuts
const Shortcuts = {
    init: function() {
        document.addEventListener('keydown', this.handleKeydown);
    },
    
    handleKeydown: function(event) {
        // Ctrl/Cmd + K for search
        if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
            event.preventDefault();
            const searchInput = document.getElementById('searchInput');
            if (searchInput) {
                searchInput.focus();
            }
        }
        
        // Escape to close modals
        if (event.key === 'Escape') {
            const openModal = document.querySelector('.modal.show');
            if (openModal) {
                const modal = bootstrap.Modal.getInstance(openModal);
                if (modal) modal.hide();
            }
        }
    }
};

// Inventory-specific functionality
const Inventory = {
    init: function() {
        this.setupQuickActions();
        this.setupBulkActions();
    },
    
    setupQuickActions: function() {
        // Add quick action buttons to inventory rows
        const actionButtons = document.querySelectorAll('[data-quick-action]');
        actionButtons.forEach(button => {
            button.addEventListener('click', this.handleQuickAction);
        });
    },
    
    setupBulkActions: function() {
        // Setup bulk selection checkboxes
        const selectAllCheckbox = document.getElementById('selectAll');
        const itemCheckboxes = document.querySelectorAll('.item-checkbox');
        
        if (selectAllCheckbox) {
            selectAllCheckbox.addEventListener('change', function() {
                itemCheckboxes.forEach(checkbox => {
                    checkbox.checked = this.checked;
                });
                Inventory.updateBulkActions();
            });
        }
        
        itemCheckboxes.forEach(checkbox => {
            checkbox.addEventListener('change', this.updateBulkActions);
        });
    },
    
    handleQuickAction: function(event) {
        const button = event.target.closest('[data-quick-action]');
        const action = button.dataset.quickAction;
        const sku = button.dataset.sku;
        
        switch (action) {
            case 'add-one':
                Inventory.quickAdjust(sku, 1);
                break;
            case 'remove-one':
                Inventory.quickAdjust(sku, -1);
                break;
        }
    },
    
    quickAdjust: async function(sku, delta) {
        try {
            Utils.showLoading(document.body);
            await API.adjustInventoryQuantity(sku, delta);
            Utils.showToast(`Successfully adjusted quantity by ${delta}`, 'success');
            // Reload page to show updated quantity
            window.location.reload();
        } catch (error) {
            Utils.showToast(`Error adjusting quantity: ${error.message}`, 'danger');
        } finally {
            Utils.hideLoading(document.body);
        }
    },
    
    updateBulkActions: function() {
        const selectedItems = document.querySelectorAll('.item-checkbox:checked');
        const bulkActions = document.getElementById('bulkActions');
        
        if (bulkActions) {
            bulkActions.style.display = selectedItems.length > 0 ? 'block' : 'none';
            
            const countElement = document.getElementById('selectedCount');
            if (countElement) {
                countElement.textContent = selectedItems.length;
            }
        }
    }
};

// Initialize app when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Initialize all modules
    Search.init();
    Forms.init();
    Shortcuts.init();
    Inventory.init();
    
    // Show app version in console
    console.log(`smallfactory Web Interface v${SF_APP.version}`);
    
    // Add helpful keyboard shortcut info
    console.log('Keyboard shortcuts:');
    console.log('- Ctrl/Cmd + K: Focus search');
    console.log('- Escape: Close modals');
});

// Export for global access
window.SF_APP = SF_APP;
window.Utils = Utils;
window.API = API;
