import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from ai_service.agents.core_agent import agent
import dateutil.parser # Для красивого формата времени


# --- НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="My AI Agent", page_icon="🤖")
st.title("🤖 Чат с Агентом (с историей)")

# --- ИНИЦИАЛИЗАЦИЯ ПАМЯТИ ---
if "thread_id" not in st.session_state:
    st.session_state.thread_id = "test_conversation_8" # Фиксированный ID для проверки истории
    st.session_state.messages = []

# --- ФУНКЦИЯ ЗАГРУЗКИ ИСТОРИИ ИЗ БД ---
def load_history_with_times(thread_id):
    """
    Загружает историю из базы данных и прикрепляет время к каждому сообщению.
    """
    config = {"configurable": {"thread_id": thread_id}}
    # Получаем итератор снэпшотов (от новых к старым)
    history_snapshots = list(agent.get_state_history(config))
    
    messages_with_time = []
    seen_ids = set() # Чтобы избежать дублей
    
    for snapshot in history_snapshots:
        if not snapshot.values or "messages" not in snapshot.values:
            continue
            
        msgs = snapshot.values["messages"]
        if not msgs:
            continue
        
        # В LangGraph каждый шаг обычно добавляет сообщение в конец.
        # Берем последнее сообщение из снэпшота — это то, что произошло в этот момент.
        last_msg = msgs[-1]
        
        # Генерируем уникальный ключ для проверки дублей (по ID или контенту)
        msg_id = getattr(last_msg, 'id', str(last_msg.content))
        
        if msg_id in seen_ids:
            continue
        seen_ids.add(msg_id)
        
        # ПРИКРЕПЛЯЕМ ВРЕМЯ (Хак: добавляем атрибут timestamp прямо к объекту сообщения)
        # snapshot.created_at — это строка времени из БД
        last_msg.timestamp = snapshot.created_at 
        messages_with_time.append(last_msg)
    
    # Разворачиваем, чтобы было от старых к новым
    return list(reversed(messages_with_time))

# --- ФУНКЦИЯ ОТРИСОВКИ ---
def render_message(msg):
    # Пытаемся получить время, если мы его прикрепили
    timestamp = getattr(msg, "timestamp", None)
    time_str = ""
    if timestamp:
        # Форматируем время (например: 12:45)
        try:
            dt = dateutil.parser.parse(timestamp)
            time_str = dt.strftime("%H:%M:%S")
        except:
            time_str = str(timestamp)

    # Вспомогательная функция для отображения времени под сообщением
    def show_time():
        if time_str:
            st.caption(f"🕒 {time_str}")

    if isinstance(msg, HumanMessage):
        with st.chat_message("user"):
            st.write(msg.content)
            show_time()
            
    elif isinstance(msg, AIMessage):
        with st.chat_message("assistant"):
            if msg.tool_calls:
                for tool_call in msg.tool_calls:
                    with st.status(f"🛠 Вызов: **{tool_call['name']}**", expanded=False):
                        st.write("Аргументы:")
                        st.json(tool_call['args'])
            
            if msg.content:
                st.write(msg.content)
            show_time()

    elif isinstance(msg, ToolMessage):
        with st.chat_message("assistant"):
            with st.expander(f"📄 Результат ({msg.name})", expanded=False):
                st.code(msg.content)
            show_time()

# --- 1. ПЕРВИЧНАЯ ЗАГРУЗКА ---
# Если сообщений нет в памяти, загружаем их из БД (вместе с временем!)
if not st.session_state.messages:
    st.session_state.messages = load_history_with_times(st.session_state.thread_id)

# --- 2. ОТРИСОВКА ВСЕГО ЧАТА ---
for msg in st.session_state.messages:
    render_message(msg)

# --- 3. ОБРАБОТКА НОВОГО СООБЩЕНИЯ ---
if user_input := st.chat_input("Напишите сообщение..."):
    
    # 1. Сразу показываем сообщение юзера (пока без времени из БД)
    user_msg = HumanMessage(content=user_input)
    # Временно добавляем в стейт для отображения
    st.session_state.messages.append(user_msg)
    render_message(user_msg)

    with st.spinner("Думаю..."):
        config = {"configurable": {"thread_id": st.session_state.thread_id}}
        
        # 2. Вызываем агента
        # Мы НЕ используем response['messages'] напрямую для обновления стейта,
        # потому что там нет времени.
        agent.invoke(input={"messages": [user_msg]},context={"user_name": "test"},config=config)
        
        
        # 3. ПОСЛЕ ответа перезагружаем историю из БД
        # Это гарантирует, что мы покажем точное время сохранения и результат
        st.session_state.messages = load_history_with_times(st.session_state.thread_id)
        
        # 4. Форсируем перезагрузку страницы, чтобы отрисовать всё красиво с временем
        st.rerun()