/* ===================================================================
   app.js  -  small interface behaviours

   Two things happen here:
     1. Flash messages can be closed, and the "success" ones fade on
        their own after a few seconds.
     2. Delete asks for confirmation in a dialog built into the page,
        instead of the browser's own confirm() box.
   =================================================================== */

document.addEventListener("DOMContentLoaded", function () {
  setupFlashes();
  setupDeleteDialog();
});


/* ---------- 1. flash messages ---------- */

function setupFlashes() {
  document.querySelectorAll(".flash").forEach(function (flash) {

    // a close button on every message
    var close = document.createElement("button");
    close.className = "flash-close";
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss message");
    close.textContent = "\u00D7";              // the × character
    close.addEventListener("click", function () { hide(flash); });
    flash.appendChild(close);

    // Only confirmations disappear on their own. Errors stay, because
    // you need to read them while you are correcting the form.
    if (flash.classList.contains("flash-success") ||
        flash.classList.contains("flash-info")) {
      setTimeout(function () { hide(flash); }, 5000);
    }
  });
}

function hide(el) {
  el.classList.add("flash-leaving");
  setTimeout(function () { el.remove(); }, 250);
}


/* ---------- 2. delete confirmation ---------- */

function setupDeleteDialog() {
  var dialog = document.getElementById("confirm-dialog");
  if (!dialog) return;

  var nameSlot = dialog.querySelector("[data-confirm-name]");
  var noteSlot = dialog.querySelector("[data-confirm-note]");
  var confirmBtn = dialog.querySelector("[data-confirm-yes]");
  var pendingForm = null;

  // every form that asks for confirmation opens the dialog instead
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      pendingForm = form;
      nameSlot.textContent = form.dataset.confirmName || "this record";
      noteSlot.textContent = form.dataset.confirmNote || "";
      dialog.showModal();
      confirmBtn.focus();
    });
  });

  confirmBtn.addEventListener("click", function () {
    dialog.close();
    if (pendingForm) pendingForm.submit();   // submit for real this time
  });

  dialog.querySelector("[data-confirm-no]")
        .addEventListener("click", function () {
    dialog.close();
    pendingForm = null;
  });

  // clicking the dark area outside the dialog closes it too
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) {
      dialog.close();
      pendingForm = null;
    }
  });
}



/* ===================================================================
   Purchase entry: add and remove invoice lines, and keep the totals
   up to date as you type.
   =================================================================== */

document.addEventListener("DOMContentLoaded", function () {
  var rows = document.getElementById("line-rows");
  if (!rows) return;                       // not on the purchase page

  var template = document.getElementById("line-template");
  var addButton = document.getElementById("add-line");

  addButton.addEventListener("click", function () {
    rows.appendChild(template.content.cloneNode(true));
    rows.lastElementChild.querySelector("select").focus();
    recalculate();
  });

  // one listener on the table handles every row, including new ones
  rows.addEventListener("click", function (event) {
    if (!event.target.classList.contains("remove-line")) return;
    if (rows.querySelectorAll(".line-row").length === 1) {
      clearRow(event.target.closest(".line-row"));   // keep one row on screen
    } else {
      event.target.closest(".line-row").remove();
    }
    recalculate();
  });

  rows.addEventListener("input", recalculate);

  if (!rows.querySelector(".line-row")) addButton.click();
  recalculate();


  function clearRow(row) {
    row.querySelectorAll("input").forEach(function (i) { i.value = ""; });
    row.querySelector("select").value = "";
  }

    function recalculate() {
    var total = 0;

    rows.querySelectorAll(".line-row").forEach(function (row) {
      var qty = parseFloat(row.querySelector(".qty").value) || 0;
      var cost = parseFloat(row.querySelector(".cost").value) || 0;
      var lineTotal = qty * cost;
      total += lineTotal;
      row.querySelector(".line-total").textContent = money(lineTotal);

      flagStock(row, qty);
    });

    document.getElementById("grand-total").textContent = money(total);
  }

  /* Sales page only: show the stock on hand for the chosen medicine, fill
     in its price, and colour the quantity red if it exceeds stock.
     The server checks this again before saving; this is just early warning. */
  rows.addEventListener("change", function (event) {
    if (!event.target.classList.contains("medicine-select")) return;

    var row = event.target.closest(".line-row");
    var option = event.target.selectedOptions[0];
    var stockCell = row.querySelector(".stock-cell");
    if (!stockCell) return;

    var stock = option ? option.dataset.stock : null;
    stockCell.textContent = stock === undefined || stock === null ? "\u2014" : stock;

    var price = option ? option.dataset.price : null;
    var priceInput = row.querySelector(".cost");
    if (price && !priceInput.value) priceInput.value = price;

    recalculate();
  });

  function flagStock(row, qty) {
    var stockCell = row.querySelector(".stock-cell");
    if (!stockCell) return;
    var stock = parseFloat(stockCell.textContent);
    var over = !isNaN(stock) && qty > stock;
    row.querySelector(".qty").classList.toggle("over-stock", over);
    stockCell.classList.toggle("over-stock", over);
  }

  function money(value) {
    return value.toLocaleString(undefined, {minimumFractionDigits: 2,
                                            maximumFractionDigits: 2});
  }
});




/* ===================================================================
   Expandable table rows: clicking an invoice shows the batches that
   arrived on it.
   =================================================================== */

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("tr.expandable").forEach(function (row) {
    row.setAttribute("tabindex", "0");
    row.setAttribute("role", "button");
    row.setAttribute("aria-expanded", "false");

    row.addEventListener("click", function () { toggle(row); });

    // keyboard users get the same behaviour
    row.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle(row);
      }
    });
  });

  function toggle(row) {
    var detail = document.getElementById(row.dataset.target);
    if (!detail) return;
    var opening = detail.hidden;
    detail.hidden = !opening;
    row.classList.toggle("open", opening);
    row.setAttribute("aria-expanded", opening ? "true" : "false");
  }
});