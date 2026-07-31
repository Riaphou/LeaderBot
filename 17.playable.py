import time
import random
from collections import defaultdict

# ==========================================
# ⚙️ PARAMÈTRES DU MOTEUR (OPTIMISÉS)
# ==========================================

VIC_VAL = 9999999999

MAX_SEARCH_PLIES = 17
MAX_THINK_TIME = 60
STARTING_TEAM = 'ally' # 'enemy'
# --- PARAMÈTRES DE RECRUTEMENT ---
liste_ally = [1, 1, 1, 1]
liste_enemy = [2, 1, 1]


# --- PARAMÈTRES BEAM SEARCH ---
BEAM_WIDTH = 300
MIN_BEAM_WIDTH = 1
MAX_PRUNING_DEPTH = 4
MARGIN_PRUNING_BASE = 25
MAX_QUIESCENCE_DEPTH = 1

# --- PARAMÈTRES D'ADOUCISSEMENT EXPONENTIEL ---
DEPTH_STEP = 3             # Le fameux 'x' : application du multiplicateur toutes les 2 profondeurs
BEAM_DECAY_RATE = 0.25     # Multiplicateur du Beam Width (ex: -25% tous les DEPTH_STEP)

BEAM_WIDTH_LUT = [
    max(MIN_BEAM_WIDTH, int(BEAM_WIDTH * (BEAM_DECAY_RATE ** (ply_left // DEPTH_STEP))))
    for ply_left in range(MAX_SEARCH_PLIES + 1)
]



# --- POIDS DE L'HEURISTIQUE GLOBALE (ALLÉGÉE) ---
HEURISTIC_WEIGHTS = {
    'dist_enemy_leader_base': 10,  # Points attribués pour chaque case de proximité avec le leader adverse
    'max_dist_calc': 10            # Valeur maximale servant à inverser la distance (proche = + de points)
}

# --- PRIORITÉ D'ORDONNANCEMENT DES COUPS (BEAM SEARCH) ---
MOVE_ORDERING_BONUS = {
    'skill': 0,      # Fort bonus pour tester les compétences offensives d'abord
    'move': 0,           # Déplacement standard
    'end_turn': -5   # Énorme malus pour retarder la fin de tour au maximum
}

EXACT, LOWERBOUND, UPPERBOUND = 0, 1, 2

# ==========================================
# ⚡ PRÉCALCULS GLOBAUX
# ==========================================

ZOBRIST_KEYS = defaultdict(lambda: random.getrandbits(64))
DIRECTIONS = [(0, 1), (-1, 1), (1, 0), (1, -1), (0, -1), (-1, 0)]

VALID_HEXES = set()
VALID_HEXES.update([(1, r) for r in range(4, 8)])
VALID_HEXES.update([(2, r) for r in range(3, 8)])
VALID_HEXES.update([(3, r) for r in range(2, 8)])
VALID_HEXES.update([(4, r) for r in range(1, 8)])
VALID_HEXES.update([(5, r) for r in range(1, 7)])
VALID_HEXES.update([(6, r) for r in range(1, 6)])
VALID_HEXES.update([(7, r) for r in range(1, 5)])

DIR_MAP = {
    'n': (0, 1), 'nord': (0, 1),
    'ne': (1, 0), 'nord-est': (1, 0),
    'se': (1, -1), 'sud-est': (1, -1),
    's': (0, -1), 'sud': (0, -1),
    'so': (-1, 0), 'sud-ouest': (-1, 0),
    'no': (-1, 1), 'nord-ouest': (-1, 1)
}

PRECOMPUTED_NEIGHBORS = {}
EDGE_HEXES = set()
DIST = {}
VISIBLE_RAYS = {}

for q, r in VALID_HEXES:
    PRECOMPUTED_NEIGHBORS[(q, r)] = [(q+dq, r+dr) for dq, dr in DIRECTIONS if (q+dq, r+dr) in VALID_HEXES]
    if len(PRECOMPUTED_NEIGHBORS[(q, r)]) < 6: EDGE_HEXES.add((q, r))
    for q2, r2 in VALID_HEXES:
        DIST[((q, r), (q2, r2))] = (abs(q - q2) + abs(q + r - q2 - r2) + abs(r - r2)) // 2
    
    VISIBLE_RAYS[(q, r)] = []
    for dq, dr in DIRECTIONS:
        ray = []
        cq, cr = q + dq, r + dr
        while (cq, cr) in VALID_HEXES:
            ray.append((cq, cr))
            cq, cr = cq + dq, cr + dr
        VISIBLE_RAYS[(q, r)].append(ray)

def fmt(pos): 
    return f" {pos[0]}.{pos[1]}"

def parse_action(ui, state, legal_moves):
    if '-' in ui:
        try:
            fr_str, to_str = ui.split('-')
            start = (int(fr_str.split('.')[0]), int(fr_str.split('.')[1]))
            end = (int(to_str.split('.')[0]), int(to_str.split('.')[1]))
            return next((m for m in legal_moves if m.get('from') == start and m.get('to') == end), None)
        except:
            pass

    parts = ui.split()
    if len(parts) < 2: return None
    
    piece_name = parts[0]
    actor_pos = None
    for pos, p in state.board.items():
        if p['name'] == piece_name and p['team'] == state.current_team:
            actor_pos = pos
            break
            
    if not actor_pos: return None
    
    action_type = parts[1]

    if action_type in ['passe', 'pass']:
        return next((m for m in legal_moves if m.get('from') == actor_pos and m['type'] == 'pass'), None)
    
    if action_type in DIR_MAP:
        if len(parts) >= 3 and parts[2] in DIR_MAP:
            d1 = DIR_MAP[action_type]
            d2 = DIR_MAP[parts[2]]
            step1_pos = (actor_pos[0] + d1[0], actor_pos[1] + d1[1])
            target_pos = (step1_pos[0] + d2[0], step1_pos[1] + d2[1])
            return next((m for m in legal_moves if m.get('from') == actor_pos and m.get('to') == target_pos and m.get('path') and m['path'][0] == step1_pos and m['type'] in ['move', 'nemesis_move']), None)

        d = DIR_MAP[action_type]
        target_pos = (actor_pos[0] + d[0], actor_pos[1] + d[1])
        return next((m for m in legal_moves if m.get('from') == actor_pos and (m.get('to') == target_pos or (m['type'] == 'nemesis_move' and m.get('to') == (actor_pos[0] + 2*d[0], actor_pos[1] + 2*d[1]))) and m['type'] in ['move', 'nemesis_move']), None)

    if action_type == 'c':
        if len(parts) < 3: return None
        if piece_name in ['rod', 'nem']:
            try:
                q, r = map(int, parts[2].split('.'))
                return next((m for m in legal_moves if m.get('from') == actor_pos and m.get('to') == (q, r) and m['type'] == 'skill'), None)
            except:
                return None
                
        dir1_str = parts[2]
        if dir1_str not in DIR_MAP: return None
        d1 = DIR_MAP[dir1_str]
        
        ray_target = None
        if actor_pos in VISIBLE_RAYS:
            for ray in VISIBLE_RAYS[actor_pos]:
                if ray and (ray[0][0]-actor_pos[0], ray[0][1]-actor_pos[1]) == d1:
                    for pos in ray:
                        if pos in state.board:
                            ray_target = pos
                            break
                    break
        
        adj_target = (actor_pos[0] + d1[0], actor_pos[1] + d1[1])
        candidates = [m for m in legal_moves if m.get('from') == actor_pos and m['type'] == 'skill']
        
        if len(parts) == 3:
            for m in candidates:
                m_to = m.get('to')
                m_tgt = m.get('target')
                
                if piece_name == 'cav' and m_to == (actor_pos[0] + 2*d1[0], actor_pos[1] + 2*d1[1]): return m
                if piece_name == 'acr' and m_to == (actor_pos[0] + 2*d1[0], actor_pos[1] + 2*d1[1]): return m
                if piece_name == 'ill' and (m_to == ray_target or m_tgt == ray_target): return m
                if piece_name == 'lan' and ray_target and m_to == (ray_target[0] - d1[0], ray_target[1] - d1[1]): return m
                if piece_name == 'gar' and m_to == adj_target: return m
                
        if len(parts) == 4:
            dir2_str = parts[3]
            if dir2_str not in DIR_MAP: return None
            d2 = DIR_MAP[dir2_str]
            
            for m in candidates:
                m_to = m.get('to')
                m_tgt = m.get('target') or m.get('push_target')
                m_push = m.get('push_to')
                
                target_plus_d2 = None
                if m_tgt: target_plus_d2 = (m_tgt[0] + d2[0], m_tgt[1] + d2[1])
                
                if piece_name == 'acr':
                    jump1 = (actor_pos[0] + 2*d1[0], actor_pos[1] + 2*d1[1])
                    jump2 = (jump1[0] + 2*d2[0], jump1[1] + 2*d2[1])
                    if m_to == jump2 and m.get('path') and len(m['path']) == 2 and m['path'][0] == jump1:
                        return m
                if piece_name == 'man' and m_tgt == ray_target and m_to == target_plus_d2: return m
                if piece_name == 'tav' and m_tgt == adj_target and m_to == target_plus_d2: return m
                if piece_name == 'cog' and m_tgt == adj_target and m_push == target_plus_d2: return m
                if piece_name == 'lan' and m_tgt == ray_target and m_to == adj_target: return m
                
    return None










# ==========================================
# MOTEUR DE RÈGLES
# ==========================================

class GameState:
    def __init__(self):
        self.board = {}
        self.team_counts = {'ally': 0, 'enemy': 0}
        self.current_team = STARTING_TEAM
        self.last_moved_team = None
        self.pieces_moved_this_turn = set()
        self.zobrist_hash = 0
        self.must_play_next = None       
        self.pending_nemesis = False     
        self.interrupted_team = None

    def init_hash(self):
        self.zobrist_hash = 0
        for pos, piece in self.board.items():
            self.zobrist_hash ^= ZOBRIST_KEYS[(pos[0], pos[1], piece['name'], piece['team'])]
        for pos in self.pieces_moved_this_turn:
            self.zobrist_hash ^= ZOBRIST_KEYS[('moved', pos[0], pos[1])]
        if self.current_team == 'enemy':
            self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy']

    def get_visible_pieces(self, q, r):
        visible = []
        for ray_idx, ray in enumerate(VISIBLE_RAYS[(q, r)]):
            dq, dr = DIRECTIONS[ray_idx]
            d = 1
            for pos in ray:
                if pos in self.board:
                    visible.append({'pos': pos, 'piece': self.board[pos], 'dist': d, 'dir': (dq, dr)})
                    break
                d += 1
        return visible

    def check_victory(self):
        defeated = set()
        for pos, piece in self.board.items():
            if piece['name'] == 'lea':
                team = piece['team']
                opp = 'enemy' if team == 'ally' else 'ally'
                neighbors = PRECOMPUTED_NEIGHBORS[pos]
                
                enemy_count = 0
                free_space = False
                
                for n in neighbors:
                    if n not in self.board:
                        free_space = True
                    elif self.board[n]['team'] == opp:
                        e_name = self.board[n]['name']
                        if e_name == 'ass': enemy_count += 11
                        elif e_name in ['our', 'arc']: pass
                        else: enemy_count += 1

                if free_space and enemy_count <= 0:
                    arc_count = 0
                    for ray in VISIBLE_RAYS[pos]:
                        if len(ray) >= 2:
                            tgt = ray[1]
                            p = self.board.get(tgt)
                            if p and p['team'] == opp and p['name'] == 'arc':
                                arc_count += 1
                                if arc_count >= 2: break
                    if arc_count < 2:
                        continue 
                    enemy_count += arc_count
                else:
                    for ray in VISIBLE_RAYS[pos]:
                        if len(ray) >= 2:
                            tgt = ray[1]
                            p = self.board.get(tgt)
                            if p and p['team'] == opp and p['name'] == 'arc':
                                enemy_count += 1

                if enemy_count >= 2 or (len(neighbors) > 0 and not free_space):
                    defeated.add(team)

        if not defeated: return None
        if 'ally' in defeated and 'enemy' not in defeated: return 'enemy'
        if 'enemy' in defeated and 'ally' not in defeated: return 'ally'
        actor = self.last_moved_team if self.last_moved_team else self.current_team
        return 'enemy' if actor == 'ally' else 'ally'



    def get_max_actions_this_turn(self):
        # 1. Nombre total de pièces de l'équipe active
        total_pieces = self.team_counts[self.current_team]
        
        # 2. On compte combien de Némésis sont présentes dans cette équipe
        nem_count = sum(1 for p in self.board.values() if p['team'] == self.current_team and p['name'] == 'nem')
        
        # 3. On retire 1 action par Némésis (avec un minimum de 1 action pour ne pas bloquer le jeu)
        return max(1, total_pieces - nem_count)

    

    def make_move(self, move):
        undo = {
            'old_team': self.current_team, 
            'old_last_moved': self.last_moved_team, 
            'old_moved': set(self.pieces_moved_this_turn), 
            'board_restore': {}, 
            'old_hash': self.zobrist_hash, 
            'old_counts': self.team_counts.copy(),
            'old_must_play_next': getattr(self, 'must_play_next', None),
            'old_pending_nemesis': getattr(self, 'pending_nemesis', False),
            'old_interrupted_team': getattr(self, 'interrupted_team', None)
        }
        
        if not hasattr(self, 'must_play_next'): self.must_play_next = None
        if not hasattr(self, 'pending_nemesis'): self.pending_nemesis = False
        if not hasattr(self, 'interrupted_team'): self.interrupted_team = None

        self.last_moved_team = self.current_team

        # Gestion de la fin de tour standard
        if move['type'] == 'end_turn':
            self.current_team = 'enemy' if self.current_team == 'ally' else 'ally'
            self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy']
            for pos in self.pieces_moved_this_turn:
                self.zobrist_hash ^= ZOBRIST_KEYS[('moved', pos[0], pos[1])]
            self.pieces_moved_this_turn.clear()
            self.must_play_next = None
            return undo

        # Gestion si la Némésis est bloquée
        if move['type'] == 'nemesis_pass':
            if self.current_team != self.interrupted_team:
                self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy']
            self.current_team = self.interrupted_team
            self.pending_nemesis = False
            self.interrupted_team = None
            
            # Correction : Forcer la vérification de la fin de tour au retour
            if len(self.pieces_moved_this_turn) >= self.get_max_actions_this_turn():
                self.current_team = 'enemy' if self.current_team == 'ally' else 'ally'
                self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy'] 
                for pos in self.pieces_moved_this_turn:
                    self.zobrist_hash ^= ZOBRIST_KEYS[('moved', pos[0], pos[1])]
                self.pieces_moved_this_turn.clear()
                self.must_play_next = None
            return undo

        # Gestion si l'Ours ou le Vieillard passe son tour (action consommée, reste sur place)
        if move['type'] == 'pass':
            self.pieces_moved_this_turn.add(move['from'])
            self.zobrist_hash ^= ZOBRIST_KEYS[('moved', move['from'][0], move['from'][1])]
            
            self.must_play_next = None
            
            # Vérification si c'était la dernière pièce du tour
            if len(self.pieces_moved_this_turn) >= self.get_max_actions_this_turn():
                self.current_team = 'enemy' if self.current_team == 'ally' else 'ally'
                self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy'] 
                for pos in self.pieces_moved_this_turn:
                    self.zobrist_hash ^= ZOBRIST_KEYS[('moved', pos[0], pos[1])]
                self.pieces_moved_this_turn.clear()
            
            return undo

        lea_pos_before = {p['team']: pos for pos, p in self.board.items() if p['name'] == 'lea'}
        actor_name = self.board[move['from']]['name'] if move['from'] in self.board else None

        # --- MODIFICATIONS DU PLATEAU (Corrigé pour éviter la disparition des pièces) ---
        sqs = set([move['from'], move['to']])
        if 'push_target' in move: sqs.update([move['push_target'], move['push_to']])
        if 'target' in move: sqs.add(move['target'])
        
        for sq in sqs:
            p = self.board.get(sq)
            undo['board_restore'][sq] = p
            if p: 
                self.zobrist_hash ^= ZOBRIST_KEYS[(sq[0], sq[1], p['name'], p['team'])]
                self.team_counts[p['team']] -= 1
        
        if move.get('is_swap'):
            pc_from = self.board.pop(move['from'])
            pc_to = self.board.pop(move['to'])
            self.board[move['to']] = pc_from
            self.board[move['from']] = pc_to
        elif 'push_target' in move:
            push_pc = self.board.pop(move['push_target'])
            pc = self.board.pop(move['from'])
            self.board[move['push_to']] = push_pc
            self.board[move['to']] = pc
        elif 'target' in move:
            target_pc = self.board.pop(move['target'])
            self.board[move['to']] = target_pc
        else:
            pc = self.board.pop(move['from'])
            self.board[move['to']] = pc # Ligne cruciale qui manquait
                
        for sq in sqs:
            p = self.board.get(sq)
            if p: 
                self.zobrist_hash ^= ZOBRIST_KEYS[(sq[0], sq[1], p['name'], p['team'])]
                self.team_counts[p['team']] += 1
                
        # --- ANALYSE POST-MOUVEMENT ---
        lea_pos_after = {p['team']: pos for pos, p in self.board.items() if p['name'] == 'lea'}
        is_nemesis_move = move.get('type') == 'nemesis_move'

        if not is_nemesis_move:
            pos_mapping = {}
            if move.get('is_swap'):
                pos_mapping[move['from']] = move['to']
                pos_mapping[move['to']] = move['from']
            elif 'push_target' in move:
                pos_mapping[move['push_target']] = move['push_to']
                pos_mapping[move['from']] = move['to']
            elif 'target' in move:
                pos_mapping[move['target']] = move['to']
                pos_mapping[move['from']] = move['from'] 
            else:
                pos_mapping[move['from']] = move['to']

            new_moved_this_turn = set()
            
            for old_pos in self.pieces_moved_this_turn:
                new_pos = pos_mapping.get(old_pos, old_pos)
                if new_pos in self.board:  
                    new_moved_this_turn.add(new_pos)
            
            # Verrouillage strict du statut action consommée
            actor_final_pos = pos_mapping.get(move['from'], move['from'])
            new_moved_this_turn.add(actor_final_pos)

            for pos in self.pieces_moved_this_turn - new_moved_this_turn:
                self.zobrist_hash ^= ZOBRIST_KEYS[('moved', pos[0], pos[1])]
            for pos in new_moved_this_turn - self.pieces_moved_this_turn:
                self.zobrist_hash ^= ZOBRIST_KEYS[('moved', pos[0], pos[1])]
                
            self.pieces_moved_this_turn = new_moved_this_turn

            if actor_name == 'our':
                if any(p['name'] == 'vie' and p['team'] == self.current_team for p in self.board.values()) and not any(pos in self.pieces_moved_this_turn for pos, p in self.board.items() if p['name'] == 'vie' and p['team'] == self.current_team):
                    self.must_play_next = 'vie'
                else: self.must_play_next = None
            elif actor_name == 'vie':
                if any(p['name'] == 'our' and p['team'] == self.current_team for p in self.board.values()) and not any(pos in self.pieces_moved_this_turn for pos, p in self.board.items() if p['name'] == 'our' and p['team'] == self.current_team):
                    self.must_play_next = 'our'
                else: self.must_play_next = None
            else:
                self.must_play_next = None

        nem_trigger = None
        if not is_nemesis_move:
            for team in ['ally', 'enemy']:
                if lea_pos_before.get(team) != lea_pos_after.get(team):
                    opp = 'enemy' if team == 'ally' else 'ally'
                    if any(p['name'] == 'nem' and p['team'] == opp for p in self.board.values()):
                        nem_trigger = opp
        
        if is_nemesis_move:
            if self.current_team != self.interrupted_team:
                self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy']
            self.current_team = self.interrupted_team
            self.pending_nemesis = False
            self.interrupted_team = None
            
            if len(self.pieces_moved_this_turn) >= self.get_max_actions_this_turn():
                self.current_team = 'enemy' if self.current_team == 'ally' else 'ally'
                self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy'] 
                for pos in self.pieces_moved_this_turn:
                    self.zobrist_hash ^= ZOBRIST_KEYS[('moved', pos[0], pos[1])]
                self.pieces_moved_this_turn.clear()
                self.must_play_next = None
        
        elif nem_trigger:
            self.interrupted_team = self.current_team
            self.current_team = nem_trigger
            self.pending_nemesis = True
            if self.current_team != self.interrupted_team:
                self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy']

        else:
            if len(self.pieces_moved_this_turn) >= self.get_max_actions_this_turn():
                self.current_team = 'enemy' if self.current_team == 'ally' else 'ally'
                self.zobrist_hash ^= ZOBRIST_KEYS['team_enemy'] 
                for pos in self.pieces_moved_this_turn:
                    self.zobrist_hash ^= ZOBRIST_KEYS[('moved', pos[0], pos[1])]
                self.pieces_moved_this_turn.clear()
                self.must_play_next = None

        return undo

    def unmake_move(self, undo):
        self.current_team = undo['old_team']
        self.last_moved_team = undo['old_last_moved']
        self.pieces_moved_this_turn = undo['old_moved']
        self.team_counts = undo['old_counts']
        self.must_play_next = undo.get('old_must_play_next')
        self.pending_nemesis = undo.get('old_pending_nemesis', False)
        self.interrupted_team = undo.get('old_interrupted_team')
        
        for sq, p in undo['board_restore'].items():
            if p is None: self.board.pop(sq, None)
            else: self.board[sq] = p
        self.zobrist_hash = undo['old_hash']









def generate_moves_for_piece(state, q, r, piece):
    moves = []
    f_str = fmt((q, r))
    name = piece['name']
    team = piece['team']

    if name == 'nem': #todo à effacer si erreur
        return moves
    
    # 1. --- GÉNÉRATION DES DÉPLACEMENTS STANDARDS ---
    empty_n = [n for n in PRECOMPUTED_NEIGHBORS[(q, r)] if n not in state.board]
    has_viz = any(p['name'] == 'viz' and p['team'] == team for p in state.board.values())
    
    for n in empty_n: 
        moves.append({'type': 'move', 'team': team, 'from': (q, r), 'to': n, 'desc': f"{name}{f_str} >{fmt(n)}"})
        if name == 'lea' and has_viz:
            for sn in [x for x in PRECOMPUTED_NEIGHBORS[n] if x not in state.board and x != (q, r)]:
                moves.append({'type': 'move', 'team': team, 'from': (q, r), 'to': sn, 'path': [n, sn], 'desc': f"lea(viz){f_str} >{fmt(sn)}"})

    # 2. --- VÉRIFICATION DU GEÔLIER ---
    # Réduit au silence : empêche les compétences ci-dessous mais laisse les déplacements standards.
    if any(n in state.board and state.board[n]['team'] != team and state.board[n]['name'] == 'geo' for n in PRECOMPUTED_NEIGHBORS[(q, r)]):
        return moves

    # 3. --- GÉNÉRATION DES COMPÉTENCES ---
    if name == 'cav':
        for dq, dr in DIRECTIONS:
            n1, n2 = (q+dq, r+dr), (q+2*dq, r+2*dr)
            if n1 in VALID_HEXES and n2 in VALID_HEXES and n1 not in state.board and n2 not in state.board:
                moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'to': n2, 'desc': f"cav{f_str} charge>{fmt(n2)}"})
                
    elif name == 'cog':
        for n in PRECOMPUTED_NEIGHBORS[(q, r)]:
            if n in state.board and state.board[n]['team'] != team:
                # NOUVEAU : Vérification du Protecteur avant de pousser
                protected = state.board[n]['name'] == 'pro' or any(hn in state.board and state.board[hn]['team'] == state.board[n]['team'] and state.board[hn]['name'] == 'pro' for hn in PRECOMPUTED_NEIGHBORS[n])
                if not protected:
                    for opp in [x for x in PRECOMPUTED_NEIGHBORS[n] if x != (q, r) and x not in PRECOMPUTED_NEIGHBORS[(q, r)]]:
                        if opp not in state.board and opp in VALID_HEXES:
                            moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'to': n, 'push_target': n, 'push_to': opp, 'desc': f"cog{f_str} frappe{fmt(n)} >pousse{fmt(opp)}"})
                        
    elif name == 'acr':
        # 1. Premier saut par-dessus un pion adjacent n1
        for n1 in PRECOMPUTED_NEIGHBORS[(q, r)]:
            if n1 in state.board: # Présence d'un pion à sauter
                dq1, dr1 = n1[0] - q, n1[1] - r
                jump1 = (n1[0] + dq1, n1[1] + dr1)
                
                # Vérification que la case d'arrivée du 1er saut est libre
                if jump1 in VALID_HEXES and jump1 not in state.board:
                    # Option A : Saut simple (1 seul saut)
                    moves.append({
                        'type': 'skill', 'team': team, 'from': (q, r), 'to': jump1, 
                        'path': [jump1], 'desc': f"acr{f_str} saute>{fmt(jump1)}"
                    })
                    
                    # Option B : Deuxième saut consécutif depuis jump1
                    for n2 in PRECOMPUTED_NEIGHBORS[jump1]:
                        # Pion à sauter (différent de la case de départ initiale)
                        if n2 in state.board and n2 != (q, r):
                            dq2, dr2 = n2[0] - jump1[0], n2[1] - jump1[1]
                            jump2 = (n2[0] + dq2, n2[1] + dr2)
                            
                            # Case d'arrivée du 2e saut libre et valide
                            if jump2 in VALID_HEXES and jump2 not in state.board and jump2 != (q, r):
                                moves.append({
                                    'type': 'skill', 'team': team, 'from': (q, r), 'to': jump2, 
                                    'path': [jump1, jump2], 'desc': f"acr{f_str} double-saute>{fmt(jump2)}"
                                })

    elif name == 'gar':
        leader_pos = next((pos for pos, p in state.board.items() if p['name'] == 'lea' and p['team'] == team), None)
        if leader_pos:
            for t_hex in [ln for ln in PRECOMPUTED_NEIGHBORS[leader_pos] if ln not in state.board or ln == (q, r)]:
                moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'to': t_hex, 'desc': f"gar{f_str} TP leader>{fmt(t_hex)}"})
                for final_hex in [x for x in PRECOMPUTED_NEIGHBORS[t_hex] if (x not in state.board or x == (q, r)) and x != t_hex]:
                    moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'to': final_hex, 'desc': f"gar{f_str} TP leader>{fmt(t_hex)} puis>{fmt(final_hex)}"})
                    
    elif name == 'tav':
        for n in PRECOMPUTED_NEIGHBORS[(q, r)]:
            if n in state.board and state.board[n]['team'] == team:
                for tn in [x for x in PRECOMPUTED_NEIGHBORS[n] if x not in state.board and x != (q, r)]:
                    moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'target': n, 'to': tn, 'desc': f"tav{f_str} bouge allié{fmt(n)}>{fmt(tn)}"})
                    
    elif name == 'rod':
        for h in VALID_HEXES:
            if h not in state.board and not any(hn in state.board and state.board[hn]['team'] != team for hn in PRECOMPUTED_NEIGHBORS[h]):
                moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'to': h, 'desc': f"rod{f_str} furtif>{fmt(h)}"})
                
    elif name == 'lan':
        for v in state.get_visible_pieces(q, r):
            if v['dist'] > 1:
                t_pos, t_piece = v['pos'], v['piece']
                dir_q, dir_r = v['dir']
                
                # Le lancier se déplace (il n'est pas bloqué par le protecteur pour s'hisser)
                land_pos = (t_pos[0] - dir_q, t_pos[1] - dir_r)
                if land_pos != (q, r) and land_pos not in state.board and land_pos in VALID_HEXES:
                    moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'to': land_pos, 'desc': f"lan{f_str} hisse vers{fmt(t_pos)}"})
                
                # Le lancier attire
                pull_pos = (q + dir_q, r + dir_r)
                if pull_pos != t_pos and pull_pos not in state.board and pull_pos in VALID_HEXES:
                    protected = t_piece['name'] == 'pro' or any(hn in state.board and state.board[hn]['team'] == t_piece['team'] and state.board[hn]['name'] == 'pro' for hn in PRECOMPUTED_NEIGHBORS[t_pos])
                    if not (t_piece['team'] != team and protected):
                        moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'target': t_pos, 'to': pull_pos, 'desc': f"lan{f_str} attire{fmt(t_pos)}>{fmt(pull_pos)}"})

    elif name == 'ill':
        for v in state.get_visible_pieces(q, r):
            if v['dist'] > 1:
                t_pos, t_piece = v['pos'], v['piece']
                
                # NOUVEAU : Vérification du Protecteur (bloque l'échange avec un ennemi)
                if t_piece['team'] != team:
                    protected = t_piece['name'] == 'pro' or any(hn in state.board and state.board[hn]['team'] == t_piece['team'] and state.board[hn]['name'] == 'pro' for hn in PRECOMPUTED_NEIGHBORS[t_pos])
                    if protected: continue
                    
                moves.append({'type': 'skill', 'is_swap': True, 'team': team, 'from': (q, r), 'to': t_pos, 'desc': f"ill{f_str} échange avec{fmt(t_pos)}"})

    elif name == 'man':
        for v in state.get_visible_pieces(q, r):
            if v['dist'] > 1 and v['piece']['team'] != team:
                t_pos, t_piece = v['pos'], v['piece']
                protected = t_piece['name'] == 'pro' or any(hn in state.board and state.board[hn]['team'] == t_piece['team'] and state.board[hn]['name'] == 'pro' for hn in PRECOMPUTED_NEIGHBORS[t_pos])
                if not protected:
                    for tn in [x for x in PRECOMPUTED_NEIGHBORS[t_pos] if x not in state.board]:
                        moves.append({'type': 'skill', 'team': team, 'from': (q, r), 'target': t_pos, 'to': tn, 'desc': f"man{f_str} manipule{fmt(t_pos)}>{fmt(tn)}"})
                        
    return moves

def get_all_legal_moves(state): #todo
    moves = []
    

    # NEMESIS
    if getattr(state, 'pending_nemesis', False):
        for pos, p in state.board.items():
            if p['team'] == state.current_team and p['name'] == 'nem':
                q, r = pos
                f_str = fmt((q, r))
                
                nem_2_steps = []
                nem_1_step = []
                
                # Étape 1 : on boucle sur toutes les directions possibles pour le premier pas
                for dq1, dr1 in DIRECTIONS:
                    n1 = (q+dq1, r+dr1)
                    if n1 in VALID_HEXES and n1 not in state.board:
                        
                        # Sauvegarde du mouvement à 1 case (au cas où elle ne peut pas faire le second pas)
                        nem_1_step.append({
                            'type': 'nemesis_move', 
                            'team': p['team'], 
                            'from': pos, 
                            'to': n1, 
                            'path': [n1], 
                            'desc': f"nem{f_str} poursuit (1 case)>{fmt(n1)}"
                        })
                        
                        # Étape 2 : on boucle depuis n1 pour le deuxième pas
                        for dq2, dr2 in DIRECTIONS:
                            n2 = (n1[0]+dq2, n1[1]+dr2)
                            
                            # Règle : n2 doit être valide, vide, et SURTOUT différente de la case de départ
                            if n2 in VALID_HEXES and n2 not in state.board and n2 != pos:
                                nem_2_steps.append({
                                    'type': 'nemesis_move', 
                                    'team': p['team'], 
                                    'from': pos, 
                                    'to': n2, 
                                    'path': [n1, n2], 
                                    'desc': f"nem{f_str} poursuit (2 cases)>{fmt(n2)}"
                                })
                
                # Priorité stricte : 2 cases d'abord, 1 case sinon
                if nem_2_steps:
                    moves.extend(nem_2_steps)
                elif nem_1_step:
                    moves.extend(nem_1_step)
                                
        if not moves:
            moves.append({'type': 'nemesis_pass', 'team': state.current_team, 'from': None, 'to': None, 'desc': 'nem bloquée (passe)'})
        return moves

    # VIEIL OURS ET OURSON
    must_play = getattr(state, 'must_play_next', None)
    if must_play:
        for pos, p in state.board.items():
            if p['team'] == state.current_team and p['name'] == must_play and pos not in state.pieces_moved_this_turn:
                moves.extend(generate_moves_for_piece(state, pos[0], pos[1], p))
                #ajout du coup de passer son tour si vie ou 
                moves.append({
                    'type': 'pass', 
                    'team': p['team'], 
                    'from': pos, 
                    'to': pos, 
                    'desc': f"{must_play}{fmt(pos)} passe"
                })
        if moves:
            return moves

    for pos, p in state.board.items():
        if p['team'] == state.current_team and pos not in state.pieces_moved_this_turn:
            moves.extend(generate_moves_for_piece(state, pos[0], pos[1], p))
            
    moves.append({'type': 'end_turn', 'team': state.current_team, 'from': None, 'to': None, 'desc': f"🏁 Fin du tour"})
    return moves
















# ==========================================
# MOTEUR DE RECHERCHE
# ==========================================

class SearchEngine:
    def __init__(self):
        self.tt = {}
        self.nodes = 0
        self.killer_moves = defaultdict(lambda: [None, None])
        self.history_table = defaultdict(int)
        self.start_time = 0
        self.time_out = False

    def reset_data(self):
        self.tt.clear()
        self.killer_moves.clear()
        self.history_table.clear()
        self.nodes = 0
        self.time_out = False

    def evaluate(self, state):
        score = 0
        
        # 1. Repérer les leaders
        leaders = {p['team']: pos for pos, p in state.board.items() if p['name'] == 'lea'}
        ally_lea = leaders.get('ally')
        enemy_lea = leaders.get('enemy')
        
        max_d = HEURISTIC_WEIGHTS['max_dist_calc']

        # 2. Boucler sur les pièces pour calculer la distance au leader adverse
        for pos, p in state.board.items():
            val = 0
            team = p['team']
            
            # Définir qui est le leader adverse pour la pièce évaluée
            his_lea = enemy_lea if team == 'ally' else ally_lea

            # Calcul de la distance pure (plus on est proche, plus (max_d - distance) est grand)
            if his_lea: 
                val += (max_d - DIST[(pos, his_lea)]) * HEURISTIC_WEIGHTS['dist_enemy_leader_base']

            # Ajouter ou soustraire au score global selon le camp
            score += val if team == 'ally' else -val
            
        return score

    def quiescence_search(self, state, alpha, beta, is_max, qs_depth):
        self.nodes += 1
        stand_pat = self.evaluate(state)

        if is_max:
            if stand_pat >= beta: return beta
            alpha = max(alpha, stand_pat)
        else:
            if stand_pat <= alpha: return alpha
            beta = min(beta, stand_pat)

        if qs_depth == 0 or self.time_out: return stand_pat

        team = 'ally' if is_max else 'enemy'
        #todo ai remplacé le commentaire par ci dessous
        moves = get_all_legal_moves(state)
        noisy_moves = [m for m in moves if m['type'] in ['skill', 'nemesis_move']]
        '''
        noisy_moves = []
        for pos, p in state.board.items():
            if p['team'] == team and pos not in state.pieces_moved_this_turn:
                moves = generate_moves_for_piece(state, pos[0], pos[1], p)
                noisy_moves.extend([m for m in moves if m['type'] == 'skill'])
        '''
        for m in noisy_moves:
            undo = state.make_move(m)
            nxt_max = (state.current_team == 'ally')
            score = self.quiescence_search(state, alpha, beta, nxt_max, qs_depth - 1)
            state.unmake_move(undo)
            if is_max:
                if score >= beta: return beta
                alpha = max(alpha, score)
            else:
                if score <= alpha: return alpha
                beta = min(beta, score)
        return alpha if is_max else beta

    def alphabeta(self, state, depth, alpha, beta, is_max, current_max_depth):
        if time.time() - self.start_time > MAX_THINK_TIME:
            self.time_out = True
            return 0, None

        self.nodes += 1
        h, alpha_orig, beta_orig = state.zobrist_hash, alpha, beta
        tt_best_move, static_eval = None, None

        if h in self.tt:
            tt_depth, tt_val, tt_flag, tt_tree, tt_eval = self.tt[h]
            static_eval = tt_eval 
            if tt_depth >= depth:
                if tt_flag == EXACT: return tt_val, tt_tree
                elif tt_flag == LOWERBOUND: alpha = max(alpha, tt_val)
                elif tt_flag == UPPERBOUND: beta = min(beta, tt_val)
                if alpha >= beta: return tt_val, tt_tree
            if tt_tree and 'move' in tt_tree: tt_best_move = tt_tree['move']

        winner = state.check_victory()
        if winner == 'ally': return VIC_VAL + depth, None
        if winner == 'enemy': return -VIC_VAL - depth, None
        if static_eval is None: static_eval = self.evaluate(state)

        # ==========================================
        # 📉 CALCUL DES MODIFICATEURS D'ADOUCISSEMENT
        # ==========================================
        
        current_beam_width = BEAM_WIDTH_LUT[depth]

        # ==========================================
        # 🧠 INTELLIGENT MARGIN PRUNING (Corrigé pour le Mat)
        # ==========================================
        # On désactive la coupe agressive si on évalue un Mat ou si on l'a déjà détecté
        if depth <= MAX_PRUNING_DEPTH and not (winner == 'ally' or winner == 'enemy') and abs(alpha) < VIC_VAL // 2 and abs(beta) < VIC_VAL // 2:
            margin = MARGIN_PRUNING_BASE * depth 
            
            if is_max:
                if static_eval - margin >= beta: 
                    return static_eval, None
                if static_eval + margin <= alpha:
                    return self.quiescence_search(state, alpha, beta, is_max, MAX_QUIESCENCE_DEPTH), None
            else:
                if static_eval + margin <= alpha: 
                    return static_eval, None
                if static_eval - margin >= beta:
                    return self.quiescence_search(state, alpha, beta, is_max, MAX_QUIESCENCE_DEPTH), None

        if depth == 0: 
            qs_eval = self.quiescence_search(state, alpha, beta, is_max, MAX_QUIESCENCE_DEPTH)
            self.tt[h] = (depth, qs_eval, EXACT, None, static_eval)
            return qs_eval, None

        #TODO
        moves = get_all_legal_moves(state)
        '''
        team = 'ally' if is_max else 'enemy'
        moves = []
        for pos, p in state.board.items():
            if p['team'] == team and pos not in state.pieces_moved_this_turn:
                moves.extend(generate_moves_for_piece(state, pos[0], pos[1], p))
                
        moves.append({'type': 'end_turn', 'team': team, 'from': None, 'to': None, 'desc': '🏁 Fin du tour'})
        '''
        def score_move(m):
            score = 0
            if tt_best_move and m == tt_best_move: return 1000000 
            if self.killer_moves[depth][0] == m: return 900000
            if self.killer_moves[depth][1] == m: return 800000
            score += self.history_table[f"{m.get('from')}_{m.get('to')}_{m.get('type')}"]
            score += MOVE_ORDERING_BONUS.get(m['type'], 0)
            return score


        moves.sort(key=score_move, reverse=True)
        current_beam = current_beam_width if depth > MAX_PRUNING_DEPTH else max(MIN_BEAM_WIDTH, current_beam_width // 2)
        moves = moves[:current_beam]

        best_tree = None
        opt_val = -float('inf') if is_max else float('inf')

        for m in moves:
            undo = state.make_move(m)
            eval_score, sub = self.alphabeta(state, depth - 1, alpha, beta, state.current_team == 'ally', current_max_depth)
            state.unmake_move(undo)
            if self.time_out: return 0, None

            if is_max:
                if eval_score > opt_val:
                    opt_val, best_tree = eval_score, {'move': m, 'next': sub}
                alpha = max(alpha, eval_score)
                if beta <= alpha:
                    if m['type'] != 'skill':
                        self.killer_moves[depth][1] = self.killer_moves[depth][0]
                        self.killer_moves[depth][0] = m
                        self.history_table[f"{m.get('from')}_{m.get('to')}_{m.get('type')}"] += depth ** 2
                    break
            else:
                if eval_score < opt_val:
                    opt_val, best_tree = eval_score, {'move': m, 'next': sub}
                beta = min(beta, eval_score)
                if beta <= alpha:
                    if m['type'] != 'skill':
                        self.killer_moves[depth][1] = self.killer_moves[depth][0]
                        self.killer_moves[depth][0] = m
                        self.history_table[f"{m.get('from')}_{m.get('to')}_{m.get('type')}"] += depth ** 2
                    break

        tt_flag = EXACT if (alpha_orig < opt_val < beta_orig) else (UPPERBOUND if opt_val <= alpha_orig else LOWERBOUND)
        self.tt[h] = (depth, opt_val, tt_flag, best_tree, static_eval)
        return opt_val, best_tree

    def find_best_move_iterative(self, state, target_depth):
        self.start_time = time.time()
        self.time_out = False
        best_tree, best_score = None, 0
        
        depth = -1
        for d in range(1, target_depth + 1):
            # 1. Enregistrer le nombre de nœuds avant d'entrer dans la profondeur d
            nodes_start = self.nodes
            
            # On lance la recherche pour la profondeur d
            score, tree = self.alphabeta(state, d, -float('inf'), float('inf'), state.current_team == 'ally', d)
            
            # Si le temps est écoulé pendant la recherche, on annule cette itération
            if self.time_out: 
                break
                
            depth = d
            best_score, best_tree = score, tree
            
            # 2. Calculer les nœuds visités uniquement pendant ce niveau
            nodes_this_depth = self.nodes - nodes_start
            
            # 3. Print mis à jour avec les nœuds de ce niveau et le total cumulé
            print(f"Profondeur {depth} | Score: {score} | Nœuds (niveau): {nodes_this_depth:,} | Total: {self.nodes:,} | Coups Gardés: {BEAM_WIDTH_LUT[depth]}")
            
            # Arrêt prématuré sur victoire/défaite forcée
            if abs(best_score) > VIC_VAL // 2:
                break
        
        return best_score, best_tree, depth














# ==========================================
# 🎨 AFFICHAGE RP & INTERFACE
# ==========================================

def translate_move_to_human(move, state):
    if not move or move['type'] == 'end_turn': return "fin"
    fr = move.get('from')
    if not fr or fr not in state.board: return move.get('desc', '')
    
    name = state.board[fr]['name']
    team = state.board[fr]['team']
    
    def get_dir(p1, p2):
        if not p1 or not p2: return None
        dq, dr = p2[0] - p1[0], p2[1] - p1[1]
        if dq == 0 and dr != 0: return (0, 1 if dr > 0 else -1)
        if dr == 0 and dq != 0: return (1 if dq > 0 else -1, 0)
        if dq == -dr and dq != 0: return (1 if dq > 0 else -1, -1 if dq > 0 else 1)
        return None

    REV_DIR = {(0, 1): 'n', (1, 0): 'ne', (1, -1): 'se', (0, -1): 's', (-1, 0): 'so', (-1, 1): 'no'}

    def dir_str(p1, p2):
        direction = get_dir(p1, p2)
        return REV_DIR.get(direction, "?")

    def format_target(pos, default_dir=None):
        if pos in state.board and state.board[pos]['team'] != team:
            return state.board[pos]['name']
        if default_dir in REV_DIR:
            return REV_DIR[default_dir]
        return f"{pos[0]}.{pos[1]}"

    if move['type'] == 'pass':
        return f"{name} passe"

    elif move['type'] in ['move', 'nemesis_move']:
        if 'path' in move and len(move['path']) >= 2:
            d1 = dir_str(fr, move['path'][0])
            d2 = dir_str(move['path'][0], move['to'])
            return f"{name} {d1} {d2}"
        d = dir_str(fr, move['to'])
        return f"{name} {d}"
            
    elif move['type'] == 'skill':
        if name in ['rod', 'nem']: 
            return f"{name} c {format_target(move['to'])}"

        if name == 'acr':
            if 'path' in move and len(move['path']) == 2:
                # Double-saut : recalcul des deux directions
                j1, j2 = move['path'][0], move['to']
                dir1 = get_dir(fr, ((fr[0]+j1[0])//2, (fr[1]+j1[1])//2))
                dir2 = get_dir(j1, ((j1[0]+j2[0])//2, (j1[1]+j2[1])//2))
                return f"acr c {REV_DIR.get(dir1, '?')} {REV_DIR.get(dir2, '?')}"
            else:
                dir1 = get_dir(fr, ((fr[0]+move['to'][0])//2, (fr[1]+move['to'][1])//2))
                return f"acr c {REV_DIR.get(dir1, '?')}"
        
        if name in ['man', 'tav'] and 'target' in move:
            d1, d2 = get_dir(fr, move['target']), get_dir(move['target'], move['to'])
            return f"{name} c {format_target(move['target'], d1)} {format_target(move['to'], d2)}"
        
        if name == 'cog' and 'push_target' in move:
            d1, d2 = get_dir(fr, move['push_target']), get_dir(move['push_target'], move['push_to'])
            return f"{name} c {format_target(move['push_target'], d1)} {format_target(move['push_to'], d2)}"
        
        main_tgt = move.get('target') if name == 'lan' and 'target' in move else move.get('to')
        d1 = get_dir(fr, main_tgt)
        return f"{name} c {format_target(main_tgt, d1)}"
        
    return move.get('desc', '')

def display_rp_board(state, tree, score):
    print("\n" + "═"*55)
    print("⚔️   CONSEIL DU STRATÈGE — PLAN D'ATTAQUE ALLIÉ   ⚔️")
    print("═"*55)
    
    if not tree or 'move' not in tree:
        print("⚠️ Aucune tactique avantageuse trouvée.")
        return

    ally_moves = []
    enemy_moves = []
    curr = tree
    while curr and 'move' in curr:
        m = curr['move']
        if m['team'] == 'ally': ally_moves.append(m['desc'])
        else: enemy_moves.append(m['desc'])
        curr = curr.get('next')

    first_move = tree['move']
    human_format = translate_move_to_human(first_move, state)
    rec_move_desc = ally_moves[0] if ally_moves else "Aucun"

    print(f"\n🎯 ACTION IMMÉDIATE CONSEILLÉE :")
    print(f"   👉  \033[1;32m[{human_format}]\033[0m  (Détail : {rec_move_desc}) | Éval: {score}")

    print("\n📜 ORDRES DE BATAILLE POUR CE TOUR (ALLIÉS) :")
    for idx, move_desc in enumerate(ally_moves, 1):
        if idx == 1:
            print(f"   {idx}. 🟦 {move_desc}  ◄ [À JOUER MAINTENANT]")
        else:
            print(f"   {idx}. 🟦 {move_desc}")

    if enemy_moves:
        print("\n🪓 RIPOSTE ENNEMIE ESTIMÉE AU PROCHAIN TOUR :")
        for idx, move_desc in enumerate(enemy_moves, 1):
            print(f"   {idx}. 🟥 {move_desc}")
            
    print("═"*55)

def parse_initial_input(prompt_text, team):
    print(f"\n--- {prompt_text} ---")
    pieces = []
    while True:
        ui = input(f"[{team.upper()}] (ex: 1.1.arc / 'fin') > ").strip()
        if ui == '':
            pos = (4, 7) if team == 'enemy' else (4, 1)
            pieces.append((pos, {'name': 'lea', 'team': team}))
            print(f"👑 Leader ('lea') placé automatiquement en {pos[0]}.{pos[1]}. Passage à la suite.")
            break
        elif ui.lower() == 'fin': break
        try:
            p = ui.split('.')
            pieces.append(((int(p[0]), int(p[1])), {'name': p[2], 'team': team}))
        except: print("❌ Format invalide.")
    return pieces

def display_board_visual(state):
    print("\n" + "═"*55)
    print(" 🗺️ CARTE TACTIQUE".center(55))
    print("═"*55)
    
    # Le plateau s'étend visuellement de y = 6 (haut) à y = -6 (bas)
    for y_vis in range(6, -7, -1):
        row_items = []
        for q, r in VALID_HEXES:
            # Calcul des décalages par rapport au centre (4.4)
            dq = q - 4
            dr = r - 4
            
            # Formule magique pour aligner les cases en hexagone
            if dr * 2 + dq == y_vis:
                pos = (q, r)
                
                if pos in state.board:
                    piece = state.board[pos]
                    team = piece['team']
                    name = piece['name']
                    
                    # Vérification des règles strictes pour savoir si la pièce peut jouer
                    can_play = (team == state.current_team and pos not in state.pieces_moved_this_turn)
                    if getattr(state, 'pending_nemesis', False) and name != 'nem':
                        can_play = False
                    must_play = getattr(state, 'must_play_next', None)
                    if must_play and name != must_play:
                        can_play = False
                        
                    # O = Jouable, X = Action consommée / Bloqué
                    char_play = 'O' if can_play else 'X'
                    
                    # Couleurs
                    color = '\033[1;34m' if team == 'ally' else '\033[1;31m'
                    reset = '\033[0m'
                    
                    # Texte de 5 caractères, ex: "O:arc"
                    text_raw = f"{char_play}:{name}"
                    text_colored = f"{color}{text_raw}{reset}"
                else:
                    text_raw = f"{q}.{r}"
                    # Gris pour les cases vides afin de ne pas surcharger la vue
                    text_colored = f"\033[90m{text_raw.center(5)}\033[0m"
                
                row_items.append((dq, text_colored, text_raw))
                
        if not row_items:
            continue
            
        # Tri de la ligne de gauche à droite
        row_items.sort(key=lambda x: x[0])
        
        line = ""
        current_idx = 0
        for dq, text_colored, text_raw in row_items:
            # Centre à l'index 25, chaque case est décalée de 5 espaces
            target_idx = 25 + dq * 5  
            spaces_needed = target_idx - current_idx
            
            if spaces_needed > 0:
                line += " " * spaces_needed
                
            line += text_colored
            current_idx = target_idx + len(text_raw)
            
        print(line)
    print("═"*55)









def get_recruit_hex(team, col_str):
    """Convertit un numéro de colonne en coordonnées sur le plateau pour l'équipe donnée."""
    try:
        c = int(col_str)
    except:
        return None
    if team == 'ally':
        # Cases alliées (1.4 2.3 3.2 4.1 5.1 6.1 7.1)
        mapping = {1: (1, 4), 2: (2, 3), 3: (3, 2), 4: (4, 1), 5: (5, 1), 6: (6, 1), 7: (7, 1)}
    else:
        # Cases ennemies (1.7 2.7 3.7 4.7 5.6 6.5 7.4)
        mapping = {1: (1, 7), 2: (2, 7), 3: (3, 7), 4: (4, 7), 5: (5, 6), 6: (6, 5), 7: (7, 4)}
    return mapping.get(c)

def handle_recruitment(state, finished_team, pending_recruits):
    """Gère la boucle interactive de recrutement pour l'équipe qui vient de finir son tour."""
    global liste_ally, liste_enemy
    recruit_list = liste_ally if finished_team == 'ally' else liste_enemy
    
    # Récupère le nombre prévu pour ce tour ou 0 si la liste est vide
    base_count = recruit_list.pop(0) if recruit_list else 0
    total_to_recruit = base_count + pending_recruits[finished_team]
    pending_recruits[finished_team] = 0
    
    if total_to_recruit > 0:
        print(f"\n🏕️ PHASE DE RECRUTEMENT [{finished_team.upper()}] - {total_to_recruit} unité(s) à recruter.")
        while total_to_recruit > 0:
            ui_rec = input(f"[{finished_team.upper()}] Recruter (Reste {total_to_recruit}) (ex: 'rod 7', 'vie 3 6', 'fin') > ").strip().lower()

            if ui_rec in ['carte', 'map', 'plateau']:
                    display_board_visual(state)
                    continue
            
            if ui_rec == 'fin':
                pending_recruits[finished_team] += total_to_recruit
                print(f"⏩ {total_to_recruit} recrue(s) reportée(s) au prochain tour.")
                break
                
            parts = ui_rec.split()
            if not parts: continue
            p_name = parts[0]
            
            # Cas spécial : Vieil-ours et Ourson
            if p_name == 'vie':
                if len(parts) != 3:
                    print("❌ Vieil-ours et Ourson nécessitent deux colonnes. Ex: 'vie 3 6'")
                    continue
                pos_vie = get_recruit_hex(finished_team, parts[1])
                pos_our = get_recruit_hex(finished_team, parts[2])
                
                if not pos_vie or not pos_our:
                    print("❌ Colonnes invalides (1 à 7).")
                    continue
                if pos_vie == pos_our:
                    print("❌ Les pions ne peuvent pas être sur la même case.")
                    continue
                if pos_vie in state.board or pos_our in state.board:
                    print("❌ Case(s) déjà occupée(s).")
                    continue
                    
                # Placement de Vieil-ours
                state.board[pos_vie] = {'name': 'vie', 'team': finished_team}
                state.team_counts[finished_team] += 1
                state.zobrist_hash ^= ZOBRIST_KEYS[(pos_vie[0], pos_vie[1], 'vie', finished_team)]
                
                # Placement de l'Ourson
                state.board[pos_our] = {'name': 'our', 'team': finished_team}
                state.team_counts[finished_team] += 1
                state.zobrist_hash ^= ZOBRIST_KEYS[(pos_our[0], pos_our[1], 'our', finished_team)]
                
                print(f"✅ Vieil-ours ({pos_vie[0]}.{pos_vie[1]}) et Ourson ({pos_our[0]}.{pos_our[1]}) recrutés.")
                total_to_recruit -= 1
                
            # Cas standard : Une seule pièce
            else:
                if len(parts) != 2:
                    print(f"❌ Format invalide. Ex: '{p_name} 4'")
                    continue
                pos = get_recruit_hex(finished_team, parts[1])
                if not pos:
                    print("❌ Colonne invalide (1 à 7).")
                    continue
                if pos in state.board:
                    print(f"❌ La case {pos[0]}.{pos[1]} est occupée.")
                    continue
                    
                # Placement standard
                state.board[pos] = {'name': p_name, 'team': finished_team}
                state.team_counts[finished_team] += 1
                state.zobrist_hash ^= ZOBRIST_KEYS[(pos[0], pos[1], p_name, finished_team)]
                
                print(f"✅ {p_name.capitalize()} recruté(e) en {pos[0]}.{pos[1]}.")
                total_to_recruit -= 1

def execute_and_post_process(state, played_move, pending_recruits):
    """Exécute un mouvement et vérifie s'il déclenche la fin du tour pour lancer le recrutement."""
    team_before = state.current_team
    was_pending_nemesis = getattr(state, 'pending_nemesis', False)
    interrupted_team_before = getattr(state, 'interrupted_team', None)
    is_explicit_end = (played_move['type'] == 'end_turn')
    is_nemesis_action = (played_move['type'] in ['nemesis_move', 'nemesis_pass'])
    
    state.make_move(played_move)
    
    if played_move['type'] not in ['end_turn', 'nemesis_pass']:
        print(f"✅ Mouvement exécuté : {played_move['desc']}")
    elif played_move['type'] == 'end_turn':
        print("✅ Fin du tour validée.")
    else:
        print("✅ La némésis passe son action.")
        
    team_after = state.current_team
    is_now_pending_nemesis = getattr(state, 'pending_nemesis', False)
    
    # CAS 1 : Fin de tour explicite ("fin")
    if is_explicit_end:
        handle_recruitment(state, team_before, pending_recruits)
        return

    # CAS 2 : Sortie d'une interruption Némésis
    if was_pending_nemesis and is_nemesis_action:
        # Si après la résolution de la némésis, le tour de l'équipe interrompue est bien fini 
        # (c'est-à-dire que le jeu est passé à l'équipe suivante), on déclenche son recrutement.
        if interrupted_team_before and team_after != interrupted_team_before:
            handle_recruitment(state, interrupted_team_before, pending_recruits)
        return

    # CAS 3 : Tour normal qui se termine naturellement sans némésis
    turnd_ended_naturally = (team_before != team_after) and not was_pending_nemesis and not is_now_pending_nemesis
    if turnd_ended_naturally:
        handle_recruitment(state, team_before, pending_recruits)
'''
def execute_and_post_process(state, played_move, pending_recruits):
    """Exécute un mouvement et vérifie s'il déclenche la fin du tour pour lancer le recrutement."""
    team_before = state.current_team
    was_pending_nemesis = getattr(state, 'pending_nemesis', False)
    is_explicit_end = (played_move['type'] == 'end_turn')
    
    state.make_move(played_move)
    
    if played_move['type'] not in ['end_turn', 'nemesis_pass']:
        print(f"✅ Mouvement exécuté : {played_move['desc']}")
    elif played_move['type'] == 'end_turn':
        print("✅ Fin du tour validée.")
    else:
        print("✅ La némésis passe son action.")
        
    team_after = state.current_team
    is_now_pending_nemesis = getattr(state, 'pending_nemesis', False)

    turnd_ended_naturally = (team_before != team_after) and not was_pending_nemesis and not is_now_pending_nemesis
    
    if is_explicit_end or turnd_ended_naturally:
        # L'équipe qui vient de finir est l'inverse de la nouvelle current_team
        finished_team = 'enemy' if state.current_team == 'ally' else 'ally'
        handle_recruitment(state, finished_team, pending_recruits)
'''







# ==========================================
# BOUCLE DE JEU PRINCIPALE
# ==========================================

def play_match():
    print("=== 🤖 BOT STRATÈGE V12 (Calcul Adaptatif & RP) ===")

    global STARTING_TEAM, liste_ally, liste_enemy

    star = input("Je commence ? (Y/n)")
    if 'n' in star:
        STARTING_TEAM = 'enemy'
        liste_ally, liste_enemy = liste_enemy, liste_ally


    enemies = parse_initial_input("ENNEMIS", "enemy")
    allies = parse_initial_input("ALLIÉS", "ally")
    
    state = GameState()
    for pos, p in enemies + allies: 
        state.board[pos] = p
        state.team_counts[p['team']] += 1
    state.init_hash()
    
    engine = SearchEngine()
    current_tree = None
    current_score = 0

    pending_recruits = {'ally': 0, 'enemy': 0}
    
    while True:
        winner = state.check_victory()
        if winner:
            print(f"\n🏆 PARTIE TERMINÉE ! Vainqueur : {winner.upper()}")
            break

        if state.current_team == 'enemy':
            print(f"\n🛡️ TOUR DE L'ÉQUIPE ENNEMIE")
            print("Entrez le coup adverse <nomEn3Char [c](compétence) [n ne se s so no](directions) [col.ligne](coordonnés)> (pour lan : <lan c n> (se diriger vers le nord)/ <lan c n s> (attirer un personnage)) ou 'fin' :")
            ui = input("🟥 [ENNEMI] > ").strip().lower()
            
            if ui == 'exit': return

            if ui in ['carte', 'map', 'plateau']:
                display_board_visual(state)
                continue

            if ui == 'fin':
                legal_moves = get_all_legal_moves(state)
                played_move = next((m for m in legal_moves if m['type'] in ['end_turn', 'nemesis_pass']), None)
                if played_move:
                    execute_and_post_process(state, played_move, pending_recruits)
                    current_tree = None  
                    continue
                else:
                    print("❌ Impossible : Action obligatoire requise (Némésis).")
                    continue

            legal_moves = get_all_legal_moves(state)
            played_move = parse_action(ui, state, legal_moves)
            
            if played_move:
                execute_and_post_process(state, played_move, pending_recruits)
                current_tree = None  
            else:
                print("❌ Mouvement illégal ou format invalide. Réessayez.")
            continue

        if state.current_team == 'ally':
            if len(state.pieces_moved_this_turn) == 0 or getattr(state, 'pending_nemesis', False):
                print(f"\n🧠 [ANALYSE TACTIQUE] L'allié calcule son plan ({MAX_THINK_TIME}s max)...")

                t0 = time.time()
                engine.reset_data()
                current_score, current_tree, depth = engine.find_best_move_iterative(state, MAX_SEARCH_PLIES)
                tps = time.time() - t0

                print(f"⏱️ Temps {tps:.2f}s .A fini de processer {depth} coups d'avance.")

            legal_moves = get_all_legal_moves(state)

            if current_tree and 'move' in current_tree and current_tree['move'] in legal_moves:
                display_rp_board(state, current_tree, current_score)
                recommended_move = current_tree['move']
                rec_move_human = translate_move_to_human(recommended_move, state)
            else:
                print("\n⚠️ Fin du plan précalculé. Vous pouvez continuer manuellement ou terminer le tour.")
                recommended_move = next((m for m in legal_moves if m['type'] in ['end_turn', 'nemesis_pass']), None)
                rec_move_human = "fin" if recommended_move else "aucun"

            played_move = None
            
            while not played_move:
                print("\nValidez le coup recommandé ou saisissez votre action :")
                if recommended_move:
                    print(f"  • Tapez 'y' ou \033[1;32m{rec_move_human}\033[0m pour appliquer ce coup")
                print("  • Tapez 'fin' pour terminer votre tour")
                print("  • Action manuelle (ex: arc ne, man c n s, rod c 4.5)")
                ui = input("🟦 [ALLIÉ] > ").strip().lower()
                
                if ui == 'exit': return

                if ui in ['carte', 'map', 'plateau']:
                    display_board_visual(state)
                    continue

                if ui == 'y' or (rec_move_human != "aucun" and ui == rec_move_human.lower()):
                    if recommended_move:
                        played_move = recommended_move
                        break
                    else:
                        print("❌ Le coup recommandé n'est plus valide.")
                        continue
                if ui == 'fin':
                    played_move = next((m for m in legal_moves if m['type'] in ['end_turn', 'nemesis_pass']), None)
                    if not played_move:
                        print("❌ Impossible : Action obligatoire (Némésis ou autre) requise.")
                        continue
                    break
                    
                played_move = parse_action(ui, state, legal_moves)
                if not played_move: 
                    print("❌ Mouvement illégal ou format invalide. Réessayez.")
            
            if played_move == recommended_move and current_tree and 'next' in current_tree:
                current_tree = current_tree['next']
            else:
                current_tree = None 
                
            execute_and_post_process(state, played_move, pending_recruits)


if __name__ == "__main__":
    play_match()