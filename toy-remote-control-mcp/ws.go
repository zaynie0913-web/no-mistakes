package main

import (
	"log"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const maxRecentMessages = 20

type ConnectionInfo struct {
	Connected      bool
	URL            string
	ConnectedAt    time.Time
	Sent           int
	Received       int
	RecentMessages []string
}

type WSClient struct {
	mu             sync.Mutex
	conn           *websocket.Conn
	connected      bool
	url            string
	connectedAt    time.Time
	sent           int
	received       int
	recentMessages []string
	done           chan struct{}
}

func NewWSClient() *WSClient {
	return &WSClient{}
}

func (c *WSClient) Connect(url string, headers map[string]string) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn != nil {
		c.conn.Close()
	}

	h := http.Header{}
	for k, v := range headers {
		h.Set(k, v)
	}

	dialer := websocket.Dialer{
		HandshakeTimeout: 10 * time.Second,
	}

	conn, _, err := dialer.Dial(url, h)
	if err != nil {
		return err
	}

	c.conn = conn
	c.connected = true
	c.url = url
	c.connectedAt = time.Now()
	c.sent = 0
	c.received = 0
	c.recentMessages = nil
	c.done = make(chan struct{})

	go c.readLoop()

	return nil
}

func (c *WSClient) readLoop() {
	defer func() {
		c.mu.Lock()
		c.connected = false
		c.mu.Unlock()
	}()

	for {
		select {
		case <-c.done:
			return
		default:
		}

		_, message, err := c.conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
				log.Printf("ws read error: %v", err)
			}
			return
		}

		c.mu.Lock()
		c.received++
		c.recentMessages = append(c.recentMessages, string(message))
		if len(c.recentMessages) > maxRecentMessages {
			c.recentMessages = c.recentMessages[len(c.recentMessages)-maxRecentMessages:]
		}
		c.mu.Unlock()
	}
}

func (c *WSClient) SendRaw(data []byte) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn == nil || !c.connected {
		return websocket.ErrCloseSent
	}

	if err := c.conn.WriteMessage(websocket.TextMessage, data); err != nil {
		return err
	}
	c.sent++
	return nil
}

func (c *WSClient) IsConnected() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.connected
}

func (c *WSClient) Info() ConnectionInfo {
	c.mu.Lock()
	defer c.mu.Unlock()

	msgs := make([]string, len(c.recentMessages))
	copy(msgs, c.recentMessages)

	return ConnectionInfo{
		Connected:      c.connected,
		URL:            c.url,
		ConnectedAt:    c.connectedAt,
		Sent:           c.sent,
		Received:       c.received,
		RecentMessages: msgs,
	}
}

func (c *WSClient) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.done != nil {
		select {
		case <-c.done:
		default:
			close(c.done)
		}
	}

	if c.conn != nil {
		c.conn.WriteMessage(
			websocket.CloseMessage,
			websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
		)
		c.conn.Close()
		c.conn = nil
	}
	c.connected = false
}
