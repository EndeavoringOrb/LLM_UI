let currentChatId = '';
let currentNodeId = '';
let currentFiles = [];
let allFiles = {};
let editingNodeId = '';
let editingNodeRole = '';
let isStreaming = false;
let currentEventSource = null;
let autoScrollEnabled = true;
let userHasScrolled = false;


// Initialize
document.addEventListener('DOMContentLoaded', function () {
    loadTools();
    loadFiles();
    loadChatList();
    setupEventListeners();
});

function setupEventListeners() {
    // Get the upload area element
    const uploadArea = document.querySelector('.upload-area');

    // Highlight the area when dragging files over it
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        uploadArea.classList.add('drag-over'); // Optional: add a visual highlight
    });

    // Remove highlight when leaving the area
    uploadArea.addEventListener('dragleave', (e) => {
        e.preventDefault();
        e.stopPropagation();
        uploadArea.classList.remove('drag-over');
    });

    // Handle dropped files
    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        uploadArea.classList.remove('drag-over');

        const files = e.dataTransfer.files;
        handleFileUpload({ target: { files } }); // reuse your existing handler
    });

    // Send button
    document.getElementById('send-btn').addEventListener('click', sendMessage);

    // Stop button
    document.getElementById('stop-btn').addEventListener('click', stopGeneration);

    // Enter key to send (Shift+Enter for new line)
    document.getElementById('message-input').addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize textarea
    document.getElementById('message-input').addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = this.scrollHeight + 'px';
    });

    // File upload
    document.getElementById('file-input').addEventListener('change', handleFileUpload);

    // Scroll monitoring for chat container
    const chatContainer = document.getElementById('chat-container');
    chatContainer.addEventListener('scroll', handleChatScroll);
}

function handleChatScroll() {
    if (!isStreaming) return;

    const chatContainer = document.getElementById('chat-container');
    const isAtBottom = chatContainer.scrollHeight - chatContainer.scrollTop <= chatContainer.clientHeight + 10;

    if (isAtBottom && !autoScrollEnabled) {
        // User scrolled back to bottom, re-enable auto-scroll
        autoScrollEnabled = true;
        userHasScrolled = false;
    } else if (!isAtBottom && autoScrollEnabled && !userHasScrolled) {
        // User scrolled up while streaming, disable auto-scroll
        autoScrollEnabled = false;
        userHasScrolled = true;
    }
}

function scrollToBottom() {
    if (!autoScrollEnabled || !isStreaming) return;

    const chatContainer = document.getElementById('chat-container');
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function stopGeneration() {
    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }

    isStreaming = false;
    autoScrollEnabled = true;
    userHasScrolled = false;

    document.getElementById('send-btn').disabled = false;
    document.getElementById('stop-btn').style.display = 'none';
    document.getElementById('send-btn').style.display = 'block';

    setStatus('Generation stopped');

    // Remove streaming indicators
    const indicators = document.querySelectorAll('.streaming-indicator');
    indicators.forEach(indicator => indicator.remove());
}

async function loadTools() {
    try {
        const response = await fetch('/api/tools');
        const data = await response.json();

        const toolsList = document.getElementById('tools-list');
        toolsList.innerHTML = '';

        data.tools.forEach(tool => {
            const toolItem = document.createElement('div');
            toolItem.className = 'tool-item';
            toolItem.innerHTML = `
                        <input type="checkbox" id="tool-${tool}" ${data.enabled[tool] ? 'checked' : ''} 
                               onchange="toggleTool('${tool}', this.checked)">
                        <label for="tool-${tool}">${tool}</label>
                    `;
            toolsList.appendChild(toolItem);
        });
    } catch (error) {
        console.error('Error loading tools:', error);
    }
}

async function toggleTool(toolName, enabled) {
    try {
        await fetch('/api/tools/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool_name: toolName, enabled })
        });
    } catch (error) {
        console.error('Error toggling tool:', error);
    }
}

async function loadFiles() {
    try {
        const response = await fetch('/api/files');
        allFiles = await response.json();

        const filesList = document.getElementById('files-list');
        filesList.innerHTML = '';

        Object.entries(allFiles).forEach(([uuid, fileInfo]) => {
            const fileItem = document.createElement('div');
            fileItem.className = 'file-item';
            fileItem.innerHTML = `
                        <span>${fileInfo.filename}</span>
                        <button class="file-remove" onclick="addFileToMessage('${uuid}')">Add</button>
                    `;
            filesList.appendChild(fileItem);
        });
    } catch (error) {
        console.error('Error loading files:', error);
    }
}

async function loadChatList() {
    try {
        const response = await fetch('/api/chats');
        const chats = await response.json();

        const chatList = document.getElementById('chat-list');
        chatList.innerHTML = '';

        if (chats.length === 0) {
            // Create first chat if none exist
            await createNewChat();
            return;
        }

        chats.forEach(chat => {
            const chatItem = document.createElement('div');
            chatItem.className = 'chat-item';
            if (chat.id === currentChatId) {
                chatItem.classList.add('active');
            }

            chatItem.innerHTML = `
                        <div class="chat-name" onclick="switchToChat('${chat.id}')">${chat.title}</div>
                        <button class="chat-delete" onclick="deleteChat('${chat.id}', event)">×</button>
                    `;
            chatList.appendChild(chatItem);
        });

        // Load the current chat if not already loaded
        if (!currentChatId && chats.length > 0) {
            await switchToChat(chats[0].id);
        }
    } catch (error) {
        console.error('Error loading chat list:', error);
    }
}

async function createNewChat() {
    try {
        const response = await fetch('/api/chats/new', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();
        if (result.success) {
            await switchToChat(result.chat_id);
            await loadChatList();
        }
    } catch (error) {
        console.error('Error creating new chat:', error);
    }
}

async function switchToChat(chatId) {
    try {
        currentChatId = chatId;

        const response = await fetch(`/api/chats/${chatId}/tree`);
        const data = await response.json();

        currentNodeId = data.current_node_id;
        document.getElementById('chat-title').textContent = data.title;

        // Clear current files when switching chats
        currentFiles = [];
        updateCurrentFilesDisplay();

        renderChatHistory(data.tree.root);

        // Update active chat in sidebar
        document.querySelectorAll('.chat-item').forEach(item => {
            item.classList.remove('active');
        });
        document.querySelector(`[onclick="switchToChat('${chatId}')"]`)?.parentElement.classList.add('active');

    } catch (error) {
        console.error('Error switching to chat:', error);
    }
}

async function deleteChat(chatId, event) {
    event.stopPropagation();

    if (!confirm('Are you sure you want to delete this chat?')) {
        return;
    }

    try {
        const response = await fetch(`/api/chats/${chatId}`, {
            method: 'DELETE'
        });

        const result = await response.json();
        if (result.success) {
            if (currentChatId === chatId) {
                // If we're deleting the current chat, switch to another or create new
                const chatListResponse = await fetch('/api/chats');
                const chats = await chatListResponse.json();
                const remainingChats = chats.filter(c => c.id !== chatId);

                if (remainingChats.length > 0) {
                    await switchToChat(remainingChats[0].id);
                } else {
                    await createNewChat();
                }
            }
            await loadChatList();
        }
    } catch (error) {
        console.error('Error deleting chat:', error);
    }
}

function renderChatHistory(node, path = []) {
    const chatContainer = document.getElementById('chat-container');

    // Clear container if this is the root call
    if (path.length === 0) {
        chatContainer.innerHTML = '';
    }

    // Add current node to path
    const currentPath = [...path, node];

    // If this node is on the path to current node, render it
    if (isOnPathToCurrent(node, currentNodeId)) {
        if (node.role !== 'system') {
            addMessageToUI(node);
        }

        // Find the child that leads to current node and render it
        const nextChild = node.children.find(child =>
            isOnPathToCurrent(child, currentNodeId) || child.id === currentNodeId
        );

        if (nextChild) {
            renderChatHistory(nextChild, currentPath);
        }
    }
}

function isOnPathToCurrent(node, targetId) {
    if (node.id === targetId) return true;

    for (const child of node.children) {
        if (isOnPathToCurrent(child, targetId)) return true;
    }

    return false;
}

function addMessageToUI(node) {
    const chatContainer = document.getElementById('chat-container');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${node.role}`;
    messageDiv.dataset.nodeId = node.id;
    messageDiv.dataset.rawContent = node.content;

    // Create the content container first
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    let contentHtml = '';

    // Handle tool calls
    if (node.tool_calls && node.tool_calls.length > 0) {
        node.tool_calls.forEach(toolCall => {
            const args = JSON.parse(toolCall.function.arguments);
            contentHtml += `<div class="tool-call"><div class="markdown-content">` + marked.parse(`**🔨 ${toolCall.function.name}**\n\n\`\`\`json\n${JSON.stringify(args, null, 2)}\n\`\`\``) + `</div></div>`;
        });
    }

    // Handle tool results
    if (node.tool_results && node.tool_results.length > 0) {
        node.tool_results.forEach(result => {
            const resultContent = typeof result.content === 'object' ? JSON.stringify(result.content, null, 2) : result.content;
            contentHtml += `<div class="tool-result"><div class="markdown-content">` + marked.parse(`**✅ Result**\n\n${resultContent}`) + `</div></div>`;
        });
    }

    // Handle main content
    if (node.content) {
        contentHtml += `<div class="markdown-content">` + marked.parse(node.content) + `</div>`;
    }

    // Handle file attachments
    let filesHtml = '';
    if (node.files && node.files.length > 0) {
        const fileNames = node.files.map(uuid => allFiles[uuid]?.filename || 'Unknown file');
        filesHtml = `<div class="message-files">📎 ${fileNames.join(', ')}</div>`;
    }

    contentDiv.innerHTML = contentHtml;
    messageDiv.appendChild(contentDiv);

    // Add files HTML if it exists
    if (filesHtml) {
        messageDiv.innerHTML += filesHtml;
    }

    // Add action buttons
    const isLastMessage = node.id === currentNodeId;
    const isAssistant = node.role === 'assistant';
    const continueButton = (isLastMessage && isAssistant) ?
        `<button class="action-btn" onclick="continueMessage('${node.id}')">Continue</button>` : '';

    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'message-actions';
    actionsDiv.innerHTML = `
        <button class="action-btn" onclick="editMessage('${node.id}', '${node.role}')">Edit</button>
        ${continueButton}
    `;
    messageDiv.appendChild(actionsDiv);

    chatContainer.appendChild(messageDiv);
    hljs.highlightAll();
}

async function handleFileUpload(event) {
    const files = event.target.files;

    for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);

        try {
            setStatus(`Uploading ${file.name}...`);
            const response = await fetch('/api/files/upload', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();
            if (result.success) {
                await loadFiles();
                setStatus(`Uploaded ${result.filename}`);
            }
        } catch (error) {
            console.error('Error uploading file:', error);
            setStatus('Upload failed');
        }
    }

    event.target.value = '';
}

function addFileToMessage(fileUuid) {
    if (!currentFiles.includes(fileUuid)) {
        currentFiles.push(fileUuid);
        updateCurrentFilesDisplay();
    }
}

function removeFileFromMessage(fileUuid) {
    currentFiles = currentFiles.filter(uuid => uuid !== fileUuid);
    updateCurrentFilesDisplay();
}

function updateCurrentFilesDisplay() {
    const container = document.getElementById('current-files');
    container.innerHTML = '';

    currentFiles.forEach(uuid => {
        const fileInfo = allFiles[uuid];
        if (fileInfo) {
            const fileDiv = document.createElement('div');
            fileDiv.className = 'current-file';
            fileDiv.innerHTML = `
                        <span>${fileInfo.filename}</span>
                        <button class="file-remove-current" onclick="removeFileFromMessage('${uuid}')">×</button>
                    `;
            container.appendChild(fileDiv);
        }
    });
}

async function sendMessage() {
    const input = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const stopBtn = document.getElementById('stop-btn');
    const message = input.value.trim();

    if (!message || isStreaming) return;

    try {
        isStreaming = true;
        autoScrollEnabled = true;
        userHasScrolled = false;

        sendBtn.style.display = 'none';
        stopBtn.style.display = 'block';
        setStatus('Sending message...');

        // Send message
        const response = await fetch(`/api/chats/${currentChatId}/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                files: [...currentFiles]
            })
        });

        const result = await response.json();
        if (!result.success) {
            throw new Error(result.error);
        }

        // Add user message to UI
        const userNode = {
            id: result.node_id,
            role: 'user',
            content: message,
            files: [...currentFiles]
        };
        addMessageToUI(userNode);

        // Clear input and files
        input.value = '';
        input.style.height = 'auto';
        currentFiles = [];
        updateCurrentFilesDisplay();
        currentNodeId = result.node_id;

        // Update chat title if this is the first message
        if (result.updated_title) {
            document.getElementById('chat-title').textContent = result.updated_title;
            await loadChatList();
        }

        // Stream response
        await streamChatResponse(result.node_id, false);

    } catch (error) {
        console.error('Error sending message:', error);
        setStatus('Error sending message');
        stopGeneration();
    }
}

async function continueMessage(nodeId) {
    if (isStreaming) return;

    try {
        isStreaming = true;
        autoScrollEnabled = true;
        userHasScrolled = false;

        const sendBtn = document.getElementById('send-btn');
        const stopBtn = document.getElementById('stop-btn');

        sendBtn.style.display = 'none';
        stopBtn.style.display = 'block';
        setStatus('Continuing generation...');

        // Call the continue endpoint
        const response = await fetch(`/api/chats/${currentChatId}/continue/${nodeId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();
        if (!result.success) {
            throw new Error(result.error);
        }

        // Stream continuation
        await streamChatResponse(nodeId, true);

    } catch (error) {
        console.error('Error continuing message:', error);
        setStatus('Error continuing message');
        stopGeneration();
    }
}

async function streamChatResponse(nodeId, isContinuation = false) {
    const eventSource = new EventSource(`/api/chats/${currentChatId}/stream/${nodeId}`);
    currentEventSource = eventSource;

    // Create assistant message element
    let assistantDiv;
    if (isContinuation) {
        // Find the existing message element
        assistantDiv = document.querySelector(`[data-node-id="${nodeId}"]`);

        // Remove continue button while streaming
        const continueBtn = assistantDiv.querySelector('button[onclick*="continueMessage"]');
        if (continueBtn) {
            continueBtn.style.display = 'none';
        }

        // Add streaming indicator
        const streamingIndicator = document.createElement('span');
        streamingIndicator.className = 'streaming-indicator';
        assistantDiv.appendChild(streamingIndicator);
    } else {
        assistantDiv = document.createElement('div');
        assistantDiv.className = 'message assistant';
        assistantDiv.innerHTML = `
                    <div class="message-content"></div>
                    <span class="streaming-indicator"></span>
                `;

        const chatContainer = document.getElementById('chat-container');
        chatContainer.appendChild(assistantDiv);
        scrollToBottom();
    }

    const timingsDiv = document.createElement('div');
    timingsDiv.className = 'message-timings';
    assistantDiv.appendChild(timingsDiv);
    const contentDiv = assistantDiv.querySelector('.message-content');
    let markdownBuffer = "";

    eventSource.onmessage = function (event) {
        const data = JSON.parse(event.data);

        switch (data.type) {
            case 'status':
                setStatus(data.content);
                break;

            case 'timings':
                timingsData = data.timings;
                if (Object.keys(timingsData).length > 0) {
                    const timingsTooltip = `
Prompt Tokens: ${timingsData.prompt_n}
Prompt Time: ${timingsData.prompt_ms.toFixed(2)} ms
Prompt per Token: ${timingsData.prompt_per_token_ms.toFixed(2)} ms
Prompt per Second: ${timingsData.prompt_per_second.toFixed(2)} tok/s
Predicted Tokens: ${timingsData.predicted_n}
Predicted Time: ${timingsData.predicted_ms.toFixed(2)} ms
Predicted per Token: ${timingsData.predicted_per_token_ms.toFixed(2)} ms
Predicted per Second: ${timingsData.predicted_per_second ? timingsData.predicted_per_second.toFixed(2) : 0} tok/s
                        `.trim();

                    timingsDiv.textContent = `${timingsData.predicted_per_second ? timingsData.predicted_per_second.toFixed(2) : 0} tok/s`;
                    timingsDiv.title = timingsTooltip;
                }
                break;

            case 'content':
                markdownBuffer += data.content;
                contentDiv.innerHTML = `<div class="markdown-content">` + marked.parse(markdownBuffer) + `</div>`;
                document.querySelectorAll('pre code').forEach((block) => {
                    delete block.dataset.highlighted;
                });
                hljs.highlightAll();
                scrollToBottom();
                break;

            case 'reasoning_content':
                // Could display reasoning in a separate area
                break;

            case 'tool_call':
                const toolCallBox = document.createElement('div');
                toolCallBox.className = 'tool-call';
                toolCallBox.innerHTML = `<div class="markdown-content">` + marked.parse(`**🔨 ${data.name}**\n\n\`\`\`json\n${JSON.stringify(data.args, null, 2)}\n\`\`\``) + `</div>`;
                assistantDiv.insertBefore(toolCallBox, contentDiv); // Insert before contentDiv
                scrollToBottom();
                break;

            case 'tool_result':
                const toolResultBox = document.createElement('div');
                toolResultBox.className = 'tool-result';
                toolResultBox.innerHTML = `<div class="markdown-content">` + marked.parse(`**✅ Result**\n\n${data.result}`) + `</div>`;
                assistantDiv.insertBefore(toolResultBox, contentDiv); // Insert before contentDiv
                scrollToBottom();
                break;

            case 'finished':
                const indicator = assistantDiv.querySelector('.streaming-indicator');
                if (indicator) indicator.remove();

                // Add action buttons - include continue since this is now the last message
                assistantDiv.dataset.nodeId = data.node_id;
                assistantDiv.dataset.rawContent = markdownBuffer;
                assistantDiv.innerHTML += `
                            <div class="message-actions">
                                <button class="action-btn" onclick="editMessage('${data.node_id}', 'assistant')">Edit</button>
                                <button class="action-btn" onclick="continueMessage('${data.node_id}')">Continue</button>
                            </div>
                        `;

                currentNodeId = data.node_id;
                stopGeneration();
                break;

            case 'error':
                const errorIndicator = assistantDiv.querySelector('.streaming-indicator');
                if (errorIndicator) errorIndicator.remove();

                contentDiv.textContent += `\n\nError: ${data.content}`;
                stopGeneration();
                break;
        }
    };

    eventSource.onerror = function (error) {
        console.error('EventSource error:', error);
        const indicator = assistantDiv.querySelector('.streaming-indicator');
        if (indicator) indicator.remove();
        stopGeneration();
    };
}

function editMessage(nodeId, role) {
    editingNodeId = nodeId;
    editingNodeRole = role;
    const messageElement = document.querySelector(`[data-node-id="${nodeId}"]`);
    const rawContent = messageElement.dataset.rawContent || '';

    document.getElementById('edit-textarea').value = rawContent;
    document.getElementById('edit-modal').classList.add('show');
}


function closeEditModal() {
    document.getElementById('edit-modal').classList.remove('show');
    editingNodeId = '';
    editingNodeRole = '';
}

async function saveEdit() {
    const newContent = document.getElementById('edit-textarea').value;

    try {
        setStatus('Saving edit...');
        const response = await fetch(`/api/chats/${currentChatId}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                node_id: editingNodeId,
                content: newContent,
                files: [] // TODO: Handle file editing
            })
        });

        const result = await response.json();
        if (result.success) {
            closeEditModal();
            await switchToChat(currentChatId);

            // If this was a user message, automatically generate a response
            if (result.should_generate) {
                try {
                    isStreaming = true;
                    autoScrollEnabled = true;
                    userHasScrolled = false;

                    const sendBtn = document.getElementById('send-btn');
                    const stopBtn = document.getElementById('stop-btn');

                    sendBtn.style.display = 'none';
                    stopBtn.style.display = 'block';
                    setStatus('Generating response...');

                    await streamChatResponse(result.node_id, false);
                } catch (error) {
                    console.error('Error generating response:', error);
                    setStatus('Error generating response');
                    stopGeneration();
                }
            }
        }
    } catch (error) {
        console.error('Error saving edit:', error);
        setStatus('Error saving edit');
    }
}

function setStatus(message) {
    document.getElementById('status-bar').textContent = message;
}