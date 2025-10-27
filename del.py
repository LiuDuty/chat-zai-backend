import sqlite3

db_name = "conversas.db"

def reset_db(path):
    conn = sqlite3.connect(path)
    try:
        cursor = conn.cursor()

        # 1) Listar todas as tabelas (excluindo tabelas do sqlite_*)
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%';
        """)
        tabelas = [row[0] for row in cursor.fetchall()]

        if tabelas:
            for tabela in tabelas:
                cursor.execute(f"DROP TABLE IF EXISTS \"{tabela}\";")
            print(f"🗑️ {len(tabelas)} tabela(s) removida(s): {', '.join(tabelas)}")
        else:
            print("ℹ️ Nenhuma tabela do usuário encontrada para remover.")

        # 2) Criar estrutura nova
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            mensagem TEXT NOT NULL,
            data_hora DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS contatos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT UNIQUE
        );
        """)

        conn.commit()
        print("📦 Banco recriado com tabelas básicas: mensagens e contatos.")
    except Exception as e:
        conn.rollback()
        print("❌ Erro ao resetar o banco:", e)
        raise
    finally:
        conn.close()
        print("✅ Conexão fechada.")

if __name__ == "__main__":
    reset_db(db_name)
