const messageList = document.getElementById('message-list');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const loadingSpinner = document.getElementById('loading-spinner');

// ADDED: Modal elements
const telegramCredsModal = document.getElementById('telegram-creds-modal');
const saveCredsButton = document.getElementById('save-telegram-creds');
const apiIdInput = document.getElementById('telegram-api-id');
const apiHashInput = document.getElementById('telegram-api-hash');
const credsErrorP = document.getElementById('creds-error');

// DEBUG: Check if spinner element is found
console.log("Spinner element:", loadingSpinner);

let eventSource = null;
let currentAssistantMessageDiv = null; // To stream into the same bubble
let currentAssistantMarkdown = ""; // Store raw Markdown for the current bubble
let streamFinishedGracefully = false; // Flag for intentional closure

function addMessage(sender, text, type = 'message') {
    const messageDiv = document.createElement('div');
    let isAssistantMessage = sender === 'assistant' && type !== 'error' && type !== 'status';

    // Reset streaming target if this is a new user message or a non-chunk assistant message
    if (sender === 'user' || (sender === 'assistant' && type === 'message')) { // Check for full assistant message too
        currentAssistantMessageDiv = null;
        currentAssistantMarkdown = ""; // Reset stored markdown
    }

    // --- Handle Streaming Assistant Message Chunks ---
    if (type === 'message_chunk' && sender === 'assistant' && currentAssistantMessageDiv) {
        currentAssistantMarkdown += text; // Append raw chunk to stored markdown
        currentAssistantMessageDiv.innerHTML = marked.parse(currentAssistantMarkdown); // Re-render markdown
        //messageList.scrollTop = messageList.scrollHeight; // Scroll down
        return; // Don't create a new div
    }

    // --- Create a new message div ---
    messageDiv.classList.add('message');
    messageDiv.classList.add(sender); // 'user' or 'assistant'

    if (type === 'error') {
        messageDiv.classList.add('error');
        messageDiv.textContent = `Error: ${text}`; // Keep error as plain text
    } else if (type === 'status') {
        messageDiv.classList.add('status');
        messageDiv.textContent = text; // Keep status as plain text
    } else if (isAssistantMessage) {
        // Handle a full (non-chunked) assistant message or start of a streamed message
        currentAssistantMarkdown = text; // Store initial markdown
        messageDiv.innerHTML = marked.parse(currentAssistantMarkdown); // Render initial markdown
    } else { // User message
        messageDiv.textContent = text; // User messages remain plain text
    }

    // If this is the start of an assistant message, store its div for streaming
    if (isAssistantMessage) {
        currentAssistantMessageDiv = messageDiv;
    }

    messageList.appendChild(messageDiv);
    messageList.scrollTop = messageList.scrollHeight; // Scroll to the bottom
}

function connectEventSource() {
    streamFinishedGracefully = false; // Reset flag on new connection
    if (eventSource) {
        eventSource.close();
    }

    // Use the sessionId passed from the template
    console.log(`Connecting to SSE stream with session ID: ${sessionId}`);
    eventSource = new EventSource(`/stream/${sessionId}`);

    eventSource.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);
            console.log('SSE Received:', data);

            // NOTE: The logic to call addMessage handles chunks vs full messages now
            if (data.type === 'message_chunk') {
                addMessage('assistant', data.content, 'message_chunk');
            } else if (data.type === 'status') {
                 console.log("Status:", data.content);
                 if (data.content === 'Finished') {
                     // Final render in case the last chunk didn't trigger it
                     if (currentAssistantMessageDiv && currentAssistantMarkdown) {
                         currentAssistantMessageDiv.innerHTML = marked.parse(currentAssistantMarkdown);
                     }
                     // Ensure spinner is hidden on finish
                     console.log("Hiding spinner: Received 'Finished' status."); // DEBUG
                     loadingSpinner.classList.add('hidden'); // ADDED
                     
                     currentAssistantMessageDiv = null;
                     currentAssistantMarkdown = ""; // Clear markdown buffer too
                     streamFinishedGracefully = true;
                 }
            } else if (data.type === 'error') {
                console.log("Hiding spinner: Received 'error' message type."); // DEBUG
                loadingSpinner.classList.add('hidden');
                addMessage('assistant', data.content, 'error');
                currentAssistantMessageDiv = null; // Stop streaming on error
                currentAssistantMarkdown = ""; // Clear markdown buffer too
            } else if (data.type === 'tool_confirm') {
                console.log("Hiding spinner: Received 'tool_confirm' message type."); // DEBUG
                loadingSpinner.classList.add('hidden');
                addMessage('system', `Tool Call Requested: ${data.tool_name} with args ${JSON.stringify(data.args)}. Confirmation UI needed.`, 'status');
                currentAssistantMessageDiv = null;
                currentAssistantMarkdown = "";
            } else if (data.type === 'message') { // Handle potential full messages if backend sends them
                console.log("Hiding spinner: Received 'message' message type (full message)."); // DEBUG
                loadingSpinner.classList.add('hidden');
                addMessage('assistant', data.content, 'message');
            }

        } catch (e) {
            console.error("Error parsing SSE message:", e);
            console.error("Raw data:", event.data);
            // Maybe add an error message to the UI here too
            addMessage('system', 'Error processing message from server.', 'error');
            currentAssistantMessageDiv = null;
            currentAssistantMarkdown = "";
        }
    };

    eventSource.onerror = function(err) {
        console.error('EventSource failed:', err);
        if (!streamFinishedGracefully) {
            addMessage('system', 'Connection error or stream closed.', 'error');
        }
        console.log("Hiding spinner: SSE onerror triggered."); // DEBUG
        eventSource.close();
        loadingSpinner.classList.add('hidden'); // ADDED - Hide on connection error
         // Optionally try to reconnect after a delay
    };
    
    eventSource.onopen = function() {
        console.log("SSE connection opened.");
    };
}

async function sendMessage() {
    const messageText = messageInput.value.trim();
    if (!messageText) return;

    addMessage('user', messageText);
    messageInput.value = '';
    adjustInputHeight(); // Reset height after sending

    loadingSpinner.classList.remove('hidden'); // ADDED - Show spinner
    messageList.scrollTop = messageList.scrollHeight;

    // Reconnect SSE before sending message to ensure it's ready
    connectEventSource(); 

    try {
        const response = await fetch('/send_message', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ message: messageText, session_id: sessionId }),
        });

        if (!response.ok) {
            const errorData = await response.json();
            console.error('Error sending message:', errorData);
            console.log("Hiding spinner: Send message fetch response not OK."); // DEBUG
            loadingSpinner.classList.add('hidden'); // ADDED - Hide on send error
            addMessage('system', `Error: ${errorData.error || response.statusText}`, 'error');
        } else {
            console.log("Message sent to backend successfully.");
            // Status updates and response chunks will arrive via SSE
        }
    } catch (error) {
        console.error('Network error sending message:', error);
        console.log("Hiding spinner: Network error during send message fetch."); // DEBUG
        loadingSpinner.classList.add('hidden'); // ADDED - Hide on network error
        addMessage('system', 'Network error sending message.', 'error');
    }
}

// --- Placeholder for sending tool confirmation back ---
// async function sendToolConfirmation(confirmed, toolName) {
//     try {
//         await fetch('/confirm_tool', { // Needs a new Flask route
//             method: 'POST',
//             headers: {'Content-Type': 'application/json'},
//             body: JSON.stringify({ session_id: sessionId, confirmed: confirmed, tool_name: toolName })
//         });
//     } catch (error) {
//         console.error('Error sending tool confirmation:', error);
//     }
// }

// --- Auto-adjust textarea height ---
function adjustInputHeight() {
    messageInput.style.height = 'auto'; // Reset height
    messageInput.style.height = (messageInput.scrollHeight) + 'px'; // Set to content height
}

messageInput.addEventListener('input', adjustInputHeight);
// Adjust height on initial load in case there's pre-filled text (unlikely here)
adjustInputHeight(); 

// --- Event Listeners ---
sendButton.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault(); // Prevent newline in textarea
        sendMessage();
    }
});

// --- ADDED: Functions to handle Telegram Credentials Modal ---

async function checkAndShowCredsModal() {
    try {
        // Check if creds already exist on backend
        const response = await fetch('/check_telegram_creds');
        if (response.ok) {
            const data = await response.json();
            if (data.exists) {
                console.log("Telegram credentials already exist.");
                telegramCredsModal.style.display = 'none';
                return; // Don't show modal if creds exist
            }
        }
        
        // Show modal if credentials don't exist
        if (telegramCredsModal) {
            console.log("Showing Telegram credentials modal.");
            telegramCredsModal.style.display = 'block'; 
        }

    } catch (error) {
        console.error('Error checking Telegram credentials:', error);
        // Show modal on error to allow user to enter credentials
        if (telegramCredsModal) {
            telegramCredsModal.style.display = 'block'; 
        }
    }
}

async function saveTelegramCredentials() {
    const apiId = apiIdInput.value.trim();
    const apiHash = apiHashInput.value.trim();
    credsErrorP.classList.add('hidden'); // Hide previous errors

    if (!apiId || !apiHash) {
        credsErrorP.textContent = 'API ID and API Hash are required.';
        credsErrorP.classList.remove('hidden');
        return;
    }

    try {
        const response = await fetch('/save_telegram_creds', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ api_id: parseInt(apiId), api_hash: apiHash }), // Send as JSON
        });

        if (!response.ok) {
            const errorData = await response.json();
            credsErrorP.textContent = `Error: ${errorData.detail || response.statusText}`;
            credsErrorP.classList.remove('hidden');
        } else {
            console.log("Telegram credentials saved successfully.");
            telegramCredsModal.style.display = 'none'; // Hide modal on success
            // Optionally enable chat input if it was disabled
        }
    } catch (error) {
        console.error('Network error saving credentials:', error);
        credsErrorP.textContent = 'Network error saving credentials.';
        credsErrorP.classList.remove('hidden');
    }
}

// --- END ADDED --- 

// Add password toggle functionality
document.addEventListener('DOMContentLoaded', () => {
    checkAndShowCredsModal(); 
    if (saveCredsButton) {
        saveCredsButton.addEventListener('click', saveTelegramCredentials);
    }

    // Add toggle password functionality
    const toggleButtons = document.querySelectorAll('.toggle-password');
    toggleButtons.forEach(button => {
        button.addEventListener('click', () => {
            const targetId = button.getAttribute('data-target');
            const input = document.getElementById(targetId);
            const isVisible = button.getAttribute('data-visible') === 'true';
            
            // Toggle input type
            input.type = isVisible ? 'password' : 'text';
            // Update button state
            button.setAttribute('data-visible', !isVisible);
            
            // Update eye icon
            const eyeIcon = button.querySelector('.eye-icon');
            if (isVisible) {
                eyeIcon.innerHTML = `
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                    <circle cx="12" cy="12" r="3"></circle>
                `;
            } else {
                eyeIcon.innerHTML = `
                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
                    <line x1="1" y1="1" x2="23" y2="23"></line>
                `;
            }
        });
    });
});

// Initial connection (optional, can also connect on first message)
// connectEventSource(); 