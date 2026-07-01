/**
 * COOP連携API通信ユーティリティ
 * 
 * 使い方:
 *   import { coopApi } from '../utils/coopApi';
 *   const ingredients = await coopApi.getIngredients();
 *   const recipes = await coopApi.suggestRecipes(['牛バラ肉', 'キャベツ']);
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// ============================================================
// 設定
// ============================================================

// サーバーURL
// TODO: アプリ側で設定画面から変更可能にする
const COOP_API_BASE = 'http://<YOUR_VPS_IP>:8003';
const API_TOKEN = '<YOUR_API_TOKEN>';

const STORAGE_KEY_COOP_SERVER = 'coop_api_server_url';
const STORAGE_KEY_COOP_TOKEN = 'coop_api_token';

// ============================================================
// APIクライアント
// ============================================================

class CoopApiClient {
  constructor() {
    this.baseUrl = COOP_API_BASE;
    this.token = API_TOKEN;
  }

  /**
   * サーバーURLをAsyncStorageから読み込み（設定画面で変更可能にする場合）
   */
  async init() {
    try {
      const savedUrl = await AsyncStorage.getItem(STORAGE_KEY_COOP_SERVER);
      if (savedUrl) this.baseUrl = savedUrl;
      const savedToken = await AsyncStorage.getItem(STORAGE_KEY_COOP_TOKEN);
      if (savedToken) this.token = savedToken;
    } catch (e) {
      // デフォルト値を使用
    }
  }

  /**
   * サーバーURL・トークンを保存
   */
  async setConfig(url, token) {
    this.baseUrl = url;
    this.token = token;
    await AsyncStorage.setItem(STORAGE_KEY_COOP_SERVER, url);
    await AsyncStorage.setItem(STORAGE_KEY_COOP_TOKEN, token);
  }

  /**
   * 共通のfetchラッパー
   */
  async _fetch(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
      ...options.headers,
    };

    try {
      const response = await fetch(url, {
        ...options,
        headers,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `APIエラー: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      if (error.message.includes('Network request failed')) {
        throw new Error('サーバーに接続できません。ネットワーク設定を確認してください。');
      }
      throw error;
    }
  }

  // ============================================================
  // API メソッド
  // ============================================================

  /**
   * 最新の食材リストを取得
   * @returns {Promise<Object>} カテゴリ分類済みの食材リスト
   */
  async getIngredients() {
    return this._fetch('/api/coop/ingredients');
  }

  /**
   * 全注文一覧を取得
   * @returns {Promise<Object>} 注文サマリーのリスト
   */
  async getOrders() {
    return this._fetch('/api/coop/orders');
  }

  /**
   * メールを手動取得
   * @param {number} daysBack - 何日前まで取得するか
   * @returns {Promise<Object>} 取得結果
   */
  async fetchEmails(daysBack = 14) {
    return this._fetch(`/api/coop/fetch?days_back=${daysBack}`, {
      method: 'POST',
    });
  }

  /**
   * 選択した食材からレシピを提案
   * @param {string[]} ingredients - 食材名のリスト
   * @param {Object} options - 追加オプション
   * @returns {Promise<Object>} レシピリスト
   */
  async suggestRecipes(ingredients, options = {}) {
    return this._fetch('/api/coop/suggest-recipes', {
      method: 'POST',
      body: JSON.stringify({
        ingredients,
        season: options.season || '',
        genre: options.genre || '',
        servings: options.servings || 2,
        mode: options.mode || 'both',
      }),
    });
  }

  /**
   * 商品のカテゴリを修正（学習用）
   * @param {string} originalName - 元の商品名
   * @param {string} category - 修正後のカテゴリ
   * @returns {Promise<Object>}
   */
  async updateCategory(originalName, category) {
    return this._fetch('/api/coop/classify', {
      method: 'PUT',
      body: JSON.stringify({
        original_name: originalName,
        category,
      }),
    });
  }

  /**
   * サーバーの接続テスト
   * @returns {Promise<boolean>}
   */
  async testConnection() {
    try {
      const data = await this._fetch('/');
      return data && data.service === 'COOP連携API';
    } catch {
      return false;
    }
  }
}

export const coopApi = new CoopApiClient();
export default coopApi;
