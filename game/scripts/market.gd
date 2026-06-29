extends Node2D

const BACKEND_HOST := "127.0.0.1"
const BACKEND_PORT := 8000
const PLAYER_ID := "p1"
const NPC_ID := "shopkeeper"
const LOCATION := "market"
const SPEED := 140.0
const TYPE_CHARS_PER_SECOND := 42.0
const INTERACT_DISTANCE := 58.0
const MAP_BOUNDS := Rect2(Vector2(-520, -300), Vector2(1040, 600))

var player: Node2D
var npc: Node2D
var camera: Camera2D
var player_velocity := Vector2.ZERO
var prompt_label: Label
var reply_label: Label
var input: LineEdit
var send_button: Button
var dialogue_panel: PanelContainer
var state_label: Label
var error_label: Label
var state_request: HTTPRequest
var talk_client: HTTPClient
var talk_active := false
var talk_requested := false
var talk_body := PackedByteArray()
var reply_buffer := ""
var reply_visible := 0.0


func _ready() -> void:
	_ensure_input_actions()
	_build_world()
	_build_ui()
	_refresh_state()


func _process(delta: float) -> void:
	if not input.has_focus():
		var direction := Input.get_vector("move_left", "move_right", "move_up", "move_down")
		player_velocity = player_velocity.lerp(direction * SPEED, min(1.0, delta * 12.0))
		player.position = (player.position + player_velocity * delta).clamp(MAP_BOUNDS.position, MAP_BOUNDS.end)
	camera.position = camera.position.lerp(player.position, min(1.0, delta * 7.0))
	prompt_label.visible = player.position.distance_to(npc.position) <= INTERACT_DISTANCE and not input.has_focus()
	if Input.is_action_just_pressed("interact") and prompt_label.visible:
		_open_dialogue()
	if talk_active:
		_poll_talk()
	_tick_typewriter(delta)


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		input.release_focus()


func _build_world() -> void:
	var grass := _pixel_texture([
		"aaaabaaaacaaaaaa",
		"aaacaaaaaaaabaaa",
		"aaaaaadaaaaaacaa",
		"aabaaaaaaaadaaaa",
		"aaaaacaaaaaabaaa",
		"aaadaaaaaacaaaaa",
		"aaaabaaaaaaaadaa",
		"acaaaaaabaaaaaaa",
		"aaaaadaaaaaacaaa",
		"aaaacaaaaabaaaaa",
		"abaaaaadaaaaaaaa",
		"aaaaaaaacaaaaaba",
		"aaacaaaaaaaadaaa",
		"aaaaabaaaaacaaaa",
		"adaaaaaaaaabaaaa",
		"aaaaacaaaaaaaada",
	], {"a": "#456f46", "b": "#547e4d", "c": "#385f3e", "d": "#6a8b54"})
	var path := _pixel_texture([
		"aaaabaaaaacaaaaa",
		"aaaacaaaaaaabaaa",
		"abaaaaadaaaaaaaa",
		"aaaaaaaacaaaaaba",
		"aaacaaaaaaaadaaa",
		"aaaaabaaaaacaaaa",
		"adaaaaaaaaabaaaa",
		"aaaaacaaaaaaaada",
		"aaaabaaaacaaaaaa",
		"aaacaaaaaaaabaaa",
		"aaaaaadaaaaaacaa",
		"aabaaaaaaaadaaaa",
		"aaaaacaaaaaabaaa",
		"aaadaaaaaacaaaaa",
		"aaaabaaaaaaaadaa",
		"acaaaaaabaaaaaaa",
	], {"a": "#8b7355", "b": "#9a8061", "c": "#735f49", "d": "#b08c65"})

	for x in range(-544, 545, 32):
		for y in range(-320, 321, 32):
			_add_sprite(Vector2(x, y), grass if abs(y - 160) > 48 and abs(x) > 96 else path, Vector2(2, 2), 0)

	_add_market_stall(Vector2(-190, -70), "#b45c3f", "#7a4f32")
	_add_market_stall(Vector2(180, 75), "#5f4b8b", "#d29c45")
	_add_barrel(Vector2(-62, 35))
	_add_barrel(Vector2(-22, 54))
	_add_crate(Vector2(272, 132))
	_add_lamp(Vector2(16, 50))
	_add_tree(Vector2(-380, -160))
	_add_tree(Vector2(392, -112))

	npc = _actor("Mira", Vector2(-170, -22), _npc_texture(), "#f6c96f")
	player = _actor("Player", Vector2(40, 160), _player_texture(), "#b9d7ff")
	camera = Camera2D.new()
	camera.zoom = Vector2(2.25, 2.25)
	camera.enabled = true
	camera.position = player.position
	add_child(camera)


func _ensure_input_actions() -> void:
	_bind_keys("move_left", [KEY_A, KEY_LEFT])
	_bind_keys("move_right", [KEY_D, KEY_RIGHT])
	_bind_keys("move_up", [KEY_W, KEY_UP])
	_bind_keys("move_down", [KEY_S, KEY_DOWN])
	_bind_keys("interact", [KEY_E])


func _bind_keys(action: String, keys: Array) -> void:
	if not InputMap.has_action(action):
		InputMap.add_action(action)
	for key in keys:
		var event := InputEventKey.new()
		event.keycode = key
		if not InputMap.action_has_event(action, event):
			InputMap.action_add_event(action, event)


func _build_ui() -> void:
	var ui := CanvasLayer.new()
	add_child(ui)

	var hud := PanelContainer.new()
	hud.position = Vector2(12, 12)
	hud.custom_minimum_size = Vector2(292, 104)
	_style_panel(hud, "#151a1ccc", "#6f8f68")
	ui.add_child(hud)
	var hud_pad := MarginContainer.new()
	hud_pad.add_theme_constant_override("margin_left", 12)
	hud_pad.add_theme_constant_override("margin_top", 10)
	hud_pad.add_theme_constant_override("margin_right", 12)
	hud_pad.add_theme_constant_override("margin_bottom", 10)
	hud.add_child(hud_pad)
	state_label = _ui_label("Loading Mira...", 15, "#f1ead2")
	state_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	hud_pad.add_child(state_label)

	prompt_label = _ui_label("E  Talk to Mira", 18, "#fff1bc")
	prompt_label.position = Vector2(388, 344)
	prompt_label.add_theme_color_override("font_shadow_color", _c("#1a1010"))
	prompt_label.add_theme_constant_override("shadow_offset_x", 2)
	prompt_label.add_theme_constant_override("shadow_offset_y", 2)
	prompt_label.visible = false
	ui.add_child(prompt_label)

	dialogue_panel = PanelContainer.new()
	dialogue_panel.position = Vector2(12, 376)
	dialogue_panel.custom_minimum_size = Vector2(936, 152)
	_style_panel(dialogue_panel, "#181315e8", "#d89a45")
	ui.add_child(dialogue_panel)
	var pad := MarginContainer.new()
	pad.add_theme_constant_override("margin_left", 16)
	pad.add_theme_constant_override("margin_top", 12)
	pad.add_theme_constant_override("margin_right", 16)
	pad.add_theme_constant_override("margin_bottom", 14)
	dialogue_panel.add_child(pad)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 9)
	pad.add_child(box)
	var name_label := _ui_label("Mira Thistlewick", 16, "#f6c96f")
	box.add_child(name_label)
	reply_label = _ui_label("The market hums under canvas awnings. Mira watches from her stall.", 16, "#f4ead7")
	reply_label.custom_minimum_size = Vector2(880, 56)
	reply_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(reply_label)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	box.add_child(row)
	input = LineEdit.new()
	input.placeholder_text = "Ask about Ashenveil..."
	input.custom_minimum_size = Vector2(0, 34)
	input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	input.text_submitted.connect(_send_message)
	row.add_child(input)
	send_button = Button.new()
	send_button.text = "Send"
	send_button.custom_minimum_size = Vector2(86, 34)
	send_button.pressed.connect(func(): _send_message(input.text))
	row.add_child(send_button)

	error_label = _ui_label("", 14, "#ffb3a7")
	error_label.position = Vector2(12, 115)
	ui.add_child(error_label)

	state_request = HTTPRequest.new()
	state_request.request_completed.connect(_on_state_response)
	add_child(state_request)


func _actor(label_text: String, pos: Vector2, texture: Texture2D, label_color: String) -> Node2D:
	var actor := Node2D.new()
	actor.position = pos
	add_child(actor)
	_add_rect_child(actor, Vector2(0, 13), Vector2(24, 7), _c("#00000055"))
	_add_sprite_child(actor, Vector2(0, -10), texture, Vector2(2, 2), 4)
	var label := _ui_label(label_text, 11, label_color)
	label.position = Vector2(-26, -52)
	label.add_theme_color_override("font_shadow_color", _c("#151010"))
	label.add_theme_constant_override("shadow_offset_x", 1)
	label.add_theme_constant_override("shadow_offset_y", 1)
	actor.add_child(label)
	return actor


func _add_rect(pos: Vector2, size: Vector2, color: Color, z: int) -> void:
	var tile := Sprite2D.new()
	tile.texture = _texture(color)
	tile.position = pos
	tile.scale = size
	tile.z_index = z
	tile.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	add_child(tile)


func _add_rect_child(parent: Node, pos: Vector2, size: Vector2, color: Color) -> void:
	var sprite := Sprite2D.new()
	sprite.texture = _texture(color)
	sprite.position = pos
	sprite.scale = size
	sprite.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	parent.add_child(sprite)


func _add_sprite(pos: Vector2, texture: Texture2D, scale: Vector2, z: int) -> Sprite2D:
	var sprite := Sprite2D.new()
	sprite.texture = texture
	sprite.position = pos
	sprite.scale = scale
	sprite.z_index = z
	sprite.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	add_child(sprite)
	return sprite


func _add_sprite_child(parent: Node, pos: Vector2, texture: Texture2D, scale: Vector2, z: int) -> Sprite2D:
	var sprite := Sprite2D.new()
	sprite.texture = texture
	sprite.position = pos
	sprite.scale = scale
	sprite.z_index = z
	sprite.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	parent.add_child(sprite)
	return sprite


func _add_market_stall(pos: Vector2, cloth: String, wood: String) -> void:
	_add_rect(pos + Vector2(0, 38), Vector2(132, 28), _c("#00000033"), 1)
	_add_rect(pos + Vector2(0, 24), Vector2(106, 58), _c(wood), 2)
	_add_rect(pos + Vector2(-42, -12), Vector2(18, 72), _c("#4b3328"), 2)
	_add_rect(pos + Vector2(42, -12), Vector2(18, 72), _c("#4b3328"), 2)
	_add_rect(pos + Vector2(0, -48), Vector2(132, 26), _c(cloth), 3)
	_add_rect(pos + Vector2(-33, -48), Vector2(4, 26), _c("#f3d37b"), 4)
	_add_rect(pos + Vector2(33, -48), Vector2(4, 26), _c("#f3d37b"), 4)
	_add_rect(pos + Vector2(-26, 7), Vector2(18, 14), _c("#ce8f54"), 4)
	_add_rect(pos + Vector2(8, 5), Vector2(24, 10), _c("#75a85b"), 4)


func _add_barrel(pos: Vector2) -> void:
	_add_rect(pos + Vector2(0, 10), Vector2(28, 8), _c("#00000044"), 1)
	_add_sprite(pos, _pixel_texture([
		".aaaa.",
		"abbbba",
		"acbbca",
		"abbbba",
		".aaaa.",
	], {"a": "#6d4a31", "b": "#9b6a3f", "c": "#d0a15d"}), Vector2(4, 4), 3)


func _add_crate(pos: Vector2) -> void:
	_add_rect(pos + Vector2(0, 13), Vector2(34, 8), _c("#00000044"), 1)
	_add_sprite(pos, _pixel_texture([
		"aaaaaa",
		"abbcca",
		"acbbca",
		"accbba",
		"aaaaaa",
	], {"a": "#6d4a31", "b": "#b37b42", "c": "#8a5e38"}), Vector2(5, 5), 3)


func _add_lamp(pos: Vector2) -> void:
	_add_rect(pos + Vector2(0, 28), Vector2(8, 68), _c("#352820"), 2)
	_add_rect(pos + Vector2(0, -10), Vector2(22, 22), _c("#f2c65b88"), 3)
	_add_rect(pos + Vector2(0, -10), Vector2(10, 10), _c("#ffd56c"), 4)


func _add_tree(pos: Vector2) -> void:
	_add_rect(pos + Vector2(0, 60), Vector2(48, 14), _c("#00000033"), 1)
	_add_rect(pos + Vector2(0, 28), Vector2(24, 76), _c("#68452e"), 2)
	_add_rect(pos + Vector2(-18, -18), Vector2(76, 54), _c("#31583a"), 3)
	_add_rect(pos + Vector2(24, -8), Vector2(60, 44), _c("#406d44"), 4)
	_add_rect(pos + Vector2(-2, -44), Vector2(62, 48), _c("#4d7d49"), 4)


func _player_texture() -> Texture2D:
	return _pixel_texture([
		"..hhhh..",
		".hsssshh",
		".sffsfss",
		".sffffs.",
		"..bbbb..",
		".bccccb.",
		".bccccb.",
		"..b..b..",
		"..d..d..",
	], {"h": "#3e2b24", "s": "#f0c7a0", "f": "#ffd9b8", "b": "#1e4f86", "c": "#2e75b6", "d": "#2b2e45"})


func _npc_texture() -> Texture2D:
	return _pixel_texture([
		"..hhhh..",
		".hhsssh.",
		".sffsfss",
		".sffffs.",
		"..mmmm..",
		".mppppm.",
		".mppppm.",
		"..m..m..",
		"..d..d..",
	], {"h": "#6b4734", "s": "#d9b16f", "f": "#f4cf91", "m": "#5b2e75", "p": "#6e3f8f", "d": "#3a273f"})


func _pixel_texture(rows: Array, palette: Dictionary) -> ImageTexture:
	var first_row: String = rows[0]
	var image := Image.create(first_row.length(), rows.size(), false, Image.FORMAT_RGBA8)
	for y in range(rows.size()):
		var row: String = rows[y]
		for x in range(row.length()):
			var key := row.substr(x, 1)
			image.set_pixel(x, y, Color.TRANSPARENT if key == "." else _c(str(palette[key])))
	return ImageTexture.create_from_image(image)


func _texture(color: Color) -> ImageTexture:
	var image := Image.create(1, 1, false, Image.FORMAT_RGBA8)
	image.set_pixel(0, 0, color)
	return ImageTexture.create_from_image(image)


func _ui_label(text: String, size: int, color: String) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", size)
	label.add_theme_color_override("font_color", _c(color))
	return label


func _style_panel(panel: PanelContainer, bg: String, border: String) -> void:
	var style := StyleBoxFlat.new()
	style.bg_color = _c(bg)
	style.border_color = _c(border)
	style.set_border_width_all(2)
	style.set_corner_radius_all(4)
	panel.add_theme_stylebox_override("panel", style)


func _c(hex: String) -> Color:
	return Color.html(hex)


func _open_dialogue() -> void:
	input.grab_focus()


func _send_message(message: String) -> void:
	message = message.strip_edges()
	if message.is_empty() or talk_active:
		return
	input.text = ""
	send_button.disabled = true
	error_label.text = ""
	reply_label.text = "Mira: "
	reply_buffer = ""
	reply_visible = 0.0
	talk_body = JSON.stringify({"player_id": PLAYER_ID, "message": message, "location": LOCATION}).to_utf8_buffer()
	talk_client = HTTPClient.new()
	talk_requested = false
	var err := talk_client.connect_to_host(BACKEND_HOST, BACKEND_PORT)
	if err != OK:
		_show_connection_error()
		return
	talk_active = true


func _poll_talk() -> void:
	talk_client.poll()
	var status := talk_client.get_status()
	if status == HTTPClient.STATUS_CONNECTED:
		if talk_requested:
			_finish_talk()
			return
		var headers := PackedStringArray(["Content-Type: application/json", "Content-Length: %d" % talk_body.size()])
		talk_client.request_raw(HTTPClient.METHOD_POST, "/npc/%s/talk" % NPC_ID, headers, talk_body)
		talk_requested = true
	elif status == HTTPClient.STATUS_BODY:
		var chunk := talk_client.read_response_body_chunk()
		if chunk.size() > 0:
			reply_buffer += chunk.get_string_from_utf8()
	elif status == HTTPClient.STATUS_DISCONNECTED:
		_finish_talk()
	elif status == HTTPClient.STATUS_CANT_CONNECT or status == HTTPClient.STATUS_CANT_RESOLVE:
		_show_connection_error()


func _finish_talk() -> void:
	talk_active = false
	send_button.disabled = false
	if reply_buffer.is_empty():
		reply_label.text = "Mira: ..."
	_refresh_state()


func _show_connection_error() -> void:
	talk_active = false
	send_button.disabled = false
	error_label.text = "Backend unavailable. Start uvicorn and try again."
	reply_label.text = "Mira is quiet. The connection failed."


func _tick_typewriter(delta: float) -> void:
	if reply_buffer.is_empty() or reply_visible >= reply_buffer.length():
		return
	reply_visible = min(reply_visible + TYPE_CHARS_PER_SECOND * delta, reply_buffer.length())
	reply_label.text = "Mira: " + reply_buffer.substr(0, int(reply_visible))


func _refresh_state() -> void:
	var url := "http://%s:%d/npc/%s/state?player_id=%s" % [BACKEND_HOST, BACKEND_PORT, NPC_ID, PLAYER_ID]
	var err := state_request.request(url)
	if err != OK:
		state_label.text = "Mira\nState unavailable"


func _on_state_response(_result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if response_code != 200:
		state_label.text = "Mira\nState unavailable"
		return
	var data = JSON.parse_string(body.get_string_from_utf8())
	if typeof(data) != TYPE_DICTIONARY:
		state_label.text = "Mira\nState unavailable"
		return
	state_label.text = "Mira Thistlewick\nDisposition: %s\nQuests: %s\nInventory: %s" % [
		data.get("disposition", "?"),
		_format_list(data.get("active_quests", []), "none"),
		_format_inventory(data.get("inventory", [])),
	]


func _format_list(items: Array, empty: String) -> String:
	if items.is_empty():
		return empty
	var text_items := []
	for item in items:
		text_items.append(str(item))
	return ", ".join(text_items)


func _format_inventory(items: Array) -> String:
	if items.is_empty():
		return "empty"
	var text_items := []
	for item in items:
		if typeof(item) == TYPE_DICTIONARY:
			text_items.append("%s x%s" % [item.get("item_id", "?"), item.get("qty", "?")])
	return ", ".join(text_items) if not text_items.is_empty() else "empty"
