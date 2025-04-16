const messageList = document.getElementById('message-list');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');

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
        messageList.scrollTop = messageList.scrollHeight; // Scroll down
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
                     currentAssistantMessageDiv = null;
                     currentAssistantMarkdown = ""; // Clear markdown buffer too
                     streamFinishedGracefully = true;
                 }
            } else if (data.type === 'error') {
                 addMessage('assistant', data.content, 'error');
                 currentAssistantMessageDiv = null; // Stop streaming on error
                 currentAssistantMarkdown = ""; // Clear markdown buffer too
            } else if (data.type === 'tool_confirm') {
                 addMessage('system', `Tool Call Requested: ${data.tool_name} with args ${JSON.stringify(data.args)}. Confirmation UI needed.`, 'status');
                 currentAssistantMessageDiv = null;
                 currentAssistantMarkdown = "";
            } else if (data.type === 'message') { // Handle potential full messages if backend sends them
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
        eventSource.close();
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
            addMessage('system', `Error: ${errorData.error || response.statusText}`, 'error');
        } else {
            console.log("Message sent to backend successfully.");
            // Status updates and response chunks will arrive via SSE
        }
    } catch (error) {
        console.error('Network error sending message:', error);
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

// Initial connection (optional, can also connect on first message)
// connectEventSource(); 