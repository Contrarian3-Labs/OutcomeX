// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT, TransferGuardBlocked} from "../src/MachineAssetNFT.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";
import {OrderRecord, OrderStatus} from "../src/types/OutcomeXTypes.sol";
import {TestBase} from "./utils/TestBase.sol";

contract OutcomeXLifecycleTest is TestBase {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant PLATFORM_TREASURY = address(0xBEEF);
    address internal constant PAYMENT_ADAPTER = address(0xAD7E2);
    address internal constant MACHINE_OWNER = address(0xCAFE);
    address internal constant BUYER = address(0xB0B);
    address internal constant BUYER_TWO = address(0xB0C);
    address internal constant RECEIVER = address(0xD00D);

    PWRToken internal pwr;
    MachineAssetNFT internal machineAsset;
    RevenueVault internal revenueVault;
    SettlementController internal settlement;
    OrderBook internal orderBook;

    uint256 internal machineId;

    function setUp() public {
        pwr = new PWRToken(ADMIN);
        machineAsset = new MachineAssetNFT(ADMIN);
        revenueVault = new RevenueVault(ADMIN, address(pwr), address(machineAsset));
        settlement = new SettlementController(ADMIN, address(revenueVault), PLATFORM_TREASURY);
        orderBook = new OrderBook(ADMIN, address(machineAsset));

        vm.startPrank(ADMIN);
        pwr.setMinter(address(revenueVault), true);
        revenueVault.setSettlementController(address(settlement));
        settlement.setOrderBook(address(orderBook));
        orderBook.setSettlementController(address(settlement));
        orderBook.setRevenueVault(address(revenueVault));
        orderBook.setPaymentAdapter(PAYMENT_ADAPTER);
        machineAsset.setTransferGuard(address(orderBook));
        machineId = machineAsset.mintMachine(MACHINE_OWNER, "ipfs://machine-001");
        vm.stopPrank();
    }

    function testConfirmLifecycleAndClaimFlow() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Confirmed), "expected confirmed status");
        assertEq(settlement.platformAccruedUSDT(), 100, "platform should receive 10%");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 900, "machine side should receive 90%");

        vm.startPrank(MACHINE_OWNER);
        vm.expectRevert(
            abi.encodeWithSelector(TransferGuardBlocked.selector, machineId, orderBook.REASON_UNSETTLED_REVENUE())
        );
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        vm.stopPrank();

        vm.prank(MACHINE_OWNER);
        uint256 claimed = revenueVault.claim(machineId);
        assertEq(claimed, 900, "machine owner claim mismatch");
        assertEq(pwr.balanceOf(MACHINE_OWNER), 900, "PWR balance mismatch");

        vm.prank(MACHINE_OWNER);
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        assertEq(machineAsset.ownerOf(machineId), RECEIVER, "ownership should transfer after claim");

        vm.prank(PLATFORM_TREASURY);
        uint256 platformClaimed = settlement.claimPlatformRevenue();
        assertEq(platformClaimed, 100, "platform claim mismatch");
    }

    function testRejectValidPreviewEconomics() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.rejectValidPreview(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Rejected), "expected rejected status");
        assertEq(settlement.refundableUSDT(BUYER), 700, "buyer refund should be 70%");
        assertEq(settlement.platformAccruedUSDT(), 30, "platform share should be 3%");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 270, "machine share should be 27%");

        vm.prank(BUYER);
        uint256 buyerClaimed = settlement.claimRefund();
        assertEq(buyerClaimed, 700, "refund claim mismatch");

        vm.prank(MACHINE_OWNER);
        uint256 machineClaimed = revenueVault.claim(machineId);
        assertEq(machineClaimed, 270, "machine claim mismatch");
        assertEq(pwr.balanceOf(MACHINE_OWNER), 270, "machine PWR mismatch");
    }

    function testRefundWhenNoValidPreviewAndActiveTaskGuard() public {
        vm.prank(BUYER_TWO);
        uint256 orderId = orderBook.createOrder(machineId, 500);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, true);

        vm.startPrank(MACHINE_OWNER);
        vm.expectRevert(abi.encodeWithSelector(TransferGuardBlocked.selector, machineId, orderBook.REASON_ACTIVE_TASK()));
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        vm.stopPrank();

        vm.prank(BUYER_TWO);
        orderBook.refundFailedOrNoValidPreview(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Refunded), "expected refunded status");
        assertEq(settlement.refundableUSDT(BUYER_TWO), 500, "buyer should receive full refund");
        assertEq(settlement.platformAccruedUSDT(), 0, "no platform fee on failed preview");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 0, "no machine accrual on failed preview");

        vm.prank(BUYER_TWO);
        uint256 buyerClaimed = settlement.claimRefund();
        assertEq(buyerClaimed, 500, "refund claim mismatch");

        vm.prank(MACHINE_OWNER);
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        assertEq(machineAsset.ownerOf(machineId), RECEIVER, "transfer should be allowed after settlement");
    }

    function testSettlementClassificationControlsDividendEligibility() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, false, false);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(settlement.platformAccruedUSDT(), 100, "platform should still receive 10%");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 0, "non-eligible order should not accrue dividends");
        assertEq(revenueVault.nonDividendRevenueByMachine(machineId), 900, "non-eligible order should track non-dividend value");

        vm.prank(MACHINE_OWNER);
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        assertEq(machineAsset.ownerOf(machineId), RECEIVER, "non-eligible order should not block transfer");
    }

    function testRefundAfterInvalidPreviewMarked() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 600);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, false);

        vm.prank(BUYER);
        orderBook.refundFailedOrNoValidPreview(orderId);

        assertEq(settlement.refundableUSDT(BUYER), 600, "full refund expected");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 0, "no machine payout expected");
        assertEq(settlement.platformAccruedUSDT(), 0, "no platform fee expected");
    }

    function testRefundPaidOrderRequiresFailureBasis() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 400);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false);

        vm.prank(BUYER);
        vm.expectRevert("REFUND_NOT_AUTHORIZED");
        orderBook.refundFailedOrNoValidPreview(orderId);
    }

    function testSettlementUsesSnapshotBeneficiaryAfterOwnershipTransfer() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.prank(MACHINE_OWNER);
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        assertEq(machineAsset.ownerOf(machineId), RECEIVER, "ownership should move before payment");

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false);

        vm.prank(RECEIVER);
        vm.expectRevert("NOT_MACHINE_OWNER");
        orderBook.markPreviewReady(orderId, true);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(revenueVault.claimableByMachineOwner(machineId, MACHINE_OWNER), 900, "snapshot owner should accrue claim");
        assertEq(revenueVault.claimableByMachineOwner(machineId, RECEIVER), 0, "new owner should not accrue old order");

        vm.startPrank(RECEIVER);
        vm.expectRevert(
            abi.encodeWithSelector(TransferGuardBlocked.selector, machineId, orderBook.REASON_UNSETTLED_REVENUE())
        );
        machineAsset.transferFrom(RECEIVER, BUYER_TWO, machineId);
        vm.stopPrank();

        vm.prank(MACHINE_OWNER);
        uint256 claimed = revenueVault.claim(machineId);
        assertEq(claimed, 900, "snapshot owner claim mismatch");

        vm.prank(RECEIVER);
        machineAsset.transferFrom(RECEIVER, BUYER_TWO, machineId);
        assertEq(machineAsset.ownerOf(machineId), BUYER_TWO, "transfer should unlock after claim");
    }
}
