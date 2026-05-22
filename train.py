import os
import random
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from model import ChessNet
from dataset import load_game_positions, GamesDataset, NUM_ACTIONS

USERNAME  = "yuandan"
PGN_FILE  = "pgns/all_games.pgn"
BATCH_SIZE = 512
EPOCHS    = 50
LR        = 1e-3
PATIENCE  = 7      # early-stopping patience (epochs without val improvement)
GRAD_CLIP = 1.0
SEED      = 42


def get_device():
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def train():
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    device = get_device()
    print(f"Using device: {device}")

    # --- Game-level split (prevents data leakage) ---
    all_games = load_game_positions(PGN_FILE, USERNAME)
    random.shuffle(all_games)

    split    = int(0.9 * len(all_games))
    train_ds = GamesDataset(all_games[:split])
    val_ds   = GamesDataset(all_games[split:])
    print(f"Train: {len(train_ds)} positions from {split} games")
    print(f"Val:   {len(val_ds)} positions from {len(all_games) - split} games")

    is_cuda = device.type == "cuda"
    is_mps  = device.type == "mps"
    num_workers = 4 if is_cuda else 0  # MPS + multiprocessing deadlocks on macOS
    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,
                              num_workers=num_workers, pin_memory=is_cuda,
                              persistent_workers=num_workers > 0)
    val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False,
                              num_workers=num_workers, pin_memory=is_cuda,
                              persistent_workers=num_workers > 0)

    model     = ChessNet(num_actions=NUM_ACTIONS).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    # ReduceLROnPlateau adapts to early stopping naturally — no T_max assumption needed
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=3, min_lr=1e-5
    )
    policy_criterion = nn.CrossEntropyLoss(label_smoothing=0.05)
    value_criterion  = nn.MSELoss()

    best_val_acc  = 0.0
    patience_left = PATIENCE
    start_epoch   = 1

    if os.path.exists("checkpoint.pt"):
        ckpt = torch.load("checkpoint.pt", map_location=device, weights_only=True)
        model.load_state_dict(ckpt["model"])
        optimizer.load_state_dict(ckpt["optimizer"])
        scheduler.load_state_dict(ckpt["scheduler"])
        start_epoch   = ckpt["epoch"] + 1
        best_val_acc  = ckpt["best_val_acc"]
        patience_left = ckpt.get("patience_left", PATIENCE)
        print(f"Resumed from checkpoint (epoch {ckpt['epoch']}, best_val={best_val_acc:.3f})")

    for epoch in range(start_epoch, EPOCHS + 1):
        # --- Training ---
        model.train()
        total_policy_loss, total_value_loss = 0.0, 0.0
        train_correct, train_total = 0, 0
        for boards, moves, outcomes in train_loader:
            boards, moves = boards.to(device), moves.to(device)
            outcomes = (outcomes.float().to(device) - 0.5) * 2  # remap: 0→-1, 0.5→0, 1→1
            optimizer.zero_grad()
            policy_logits, value = model(boards)
            policy_loss = policy_criterion(policy_logits, moves)
            value_loss  = value_criterion(value.squeeze(1), outcomes)
            loss = policy_loss + 0.5 * value_loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP)
            optimizer.step()
            total_policy_loss += policy_loss.item()
            total_value_loss  += value_loss.item()
            train_correct += (policy_logits.argmax(1) == moves).sum().item()
            train_total   += len(moves)

        # --- Validation ---
        model.eval()
        val_correct, val_total = 0, 0
        val_value_loss = 0.0
        with torch.no_grad():
            for boards, moves, outcomes in val_loader:
                boards, moves = boards.to(device), moves.to(device)
                outcomes = (outcomes.float().to(device) - 0.5) * 2
                policy_logits, value = model(boards)
                val_correct   += (policy_logits.argmax(1) == moves).sum().item()
                val_total     += len(moves)
                val_value_loss += value_criterion(value.squeeze(1), outcomes).item()

        train_acc = train_correct / train_total
        val_acc   = val_correct   / val_total
        current_lr = optimizer.param_groups[0]["lr"]
        n  = len(train_loader)
        nv = len(val_loader)
        print(f"Epoch {epoch:02d}/{EPOCHS}  policy={total_policy_loss/n:.4f}"
              f"  value={total_value_loss/n:.4f}"
              f"  val_value={val_value_loss/nv:.4f}"
              f"  train={train_acc:.3f}  val={val_acc:.3f}  lr={current_lr:.2e}")

        scheduler.step(val_acc)

        if val_acc > best_val_acc:
            best_val_acc  = val_acc
            patience_left = PATIENCE
            torch.save(model.state_dict(), "best_model.pt")
            torch.save({
                "model":        model.state_dict(),
                "optimizer":    optimizer.state_dict(),
                "scheduler":    scheduler.state_dict(),
                "epoch":        epoch,
                "best_val_acc": best_val_acc,
                "patience_left": patience_left,
            }, "checkpoint.pt")
            print(f"           ^ saved best model (val={val_acc:.3f})")
        else:
            patience_left -= 1
            if patience_left == 0:
                print(f"No improvement for {PATIENCE} epochs — stopping early.")
                break


if __name__ == "__main__":
    train()
