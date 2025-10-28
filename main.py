import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { timeout, catchError } from 'rxjs/operators';

@Injectable({
  providedIn: 'root',
})
export class ZaiService {
  private backendUrl: string;

  constructor(private http: HttpClient) {
    const baseUrl = window.location.hostname.includes('localhost')
      ? 'http://127.0.0.1:8000'
      : 'https://zai-backend.onrender.com';

    // remove barra final se houver
    this.backendUrl = baseUrl.replace(/\/$/, '');
  }

  /**
   * Envia mensagem para o backend FastAPI (Z.ai)
   * Timeout aumentado para 120s (iguais ao backend)
   */
  enviarMensagem(texto: string, sessionId: string): Observable<any> {
    return this.http
      .post(`${this.backendUrl}/mensagem`, {
        texto,
        session_id: sessionId,
      })
      .pipe(
        timeout(120000), // 2 minutos
        catchError((error) => {
          console.error('❌ Erro ao enviar mensagem:', error);
          return throwError(() => new Error('Erro ao enviar mensagem.'));
        })
      );
  }

  /**
   * Obtém o contexto atual da conversa (para restaurar histórico)
   */
  getContexto(sessionId: string): Observable<any> {
    return this.http
      .get(`${this.backendUrl}/contexto/${sessionId}`)
      .pipe(
        timeout(15000), // 15 segundos
        catchError((error) => {
          console.error('⚠️ Erro ao obter contexto:', error);
          return throwError(() => new Error('Erro ao buscar contexto.'));
        })
      );
  }
}
