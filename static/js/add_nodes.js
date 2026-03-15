// Function to add a new property field
function addPropertyField() {
    const propertiesContainer = document.getElementById('properties-container');
    const fieldCount = propertiesContainer.childElementCount;
    const newField = document.createElement('div');
    newField.innerHTML = `
        <label for="properties-${fieldCount}">Property ${fieldCount + 1}</label>
        <input type="text" name="properties-${fieldCount}" id="properties-${fieldCount}" />
    `;
    propertiesContainer.appendChild(newField);
}

// Add event listeners for adding array elements and key-value pairs
document.getElementById('add-array-element-button').addEventListener('click', addPropertyField);
document.getElementById('add-key-value-button').addEventListener('click', addPropertyField);

// Add the initial property field when the page loads
addPropertyField();
