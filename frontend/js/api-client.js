/**
 * HTTP клиент для взаимодействия с backend
 */
class APIClient {
  constructor(baseURL = '/api') {
    this.baseURL = baseURL;
  }

  async request(endpoint, options = {}) {
    const url = `${this.baseURL}${endpoint}`;
    const response = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return await response.json();
  }

  async buildOzonPayload(product, clientId, apiKey) {
    return this.request('/ozon/build', {
      method: 'POST',
      body: JSON.stringify({
        ...product,
        client_id: clientId,
        api_key: apiKey,
      }),
    });
  }

  async sendOzonRequest(product, clientId, apiKey, env = 'sandbox') {
    return this.request('/ozon/send', {
      method: 'POST',
      body: JSON.stringify({
        ...product,
        client_id: clientId,
        api_key: apiKey,
        env,
      }),
    });
  }

  async buildWBPayload(product, apiKey) {
    return this.request('/wildberries/build', {
      method: 'POST',
      body: JSON.stringify({
        ...product,
        api_key: apiKey,
      }),
    });
  }

  async sendWBRequest(product, apiKey, env = 'sandbox') {
    return this.request('/wildberries/send', {
      method: 'POST',
      body: JSON.stringify({
        ...product,
        api_key: apiKey,
        env,
      }),
    });
  }
}

const apiClient = new APIClient('/api');
